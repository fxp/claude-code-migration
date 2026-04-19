"""Redactor — scrub plaintext secrets out of scan/IR dicts before disk write.

Two mechanisms compose:

1. **Key-name lookup** (ported from neuDrive's `looksSensitiveKey`): if a dict
   key matches a sensitive-key whitelist (token/authorization/api_key/...),
   its string value is always masked.

2. **Free-form regex** (extends `secrets.py`): any string value is scanned
   for known secret patterns (sk-ant-, ghp_, Bearer <tok>, AWS AKID, etc.).
   Matches are masked even if they appear in prose — shell-snapshot bodies,
   history.jsonl `display` fields, session chat message bodies all get
   covered this way. neuDrive's approach only matches `key=value` lines,
   which misses pasted secrets inside free-form text.

Redacted values become `${CC_<UPPER_SNAKE_PATH>}` env-var references, which
the existing adapters already know how to pass through to target configs.
A findings list is returned alongside the redacted copy so callers can emit
a `secrets-manifest.json` (SHA256 prefix only, no plaintext).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict
from typing import Any


# ── Patterns ─────────────────────────────────────────────────────────

# Sensitive dict-key name substrings — if a key matches, its string value
# is always masked. Ported from agi-bar/neuDrive
# internal/platforms/claude_migration.go looksSensitiveKey, extended.
_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "token", "secret", "password", "api_key", "apikey",
    "authorization", "bearer", "appkey", "appsecret",
    "client_secret", "credential", "auth_token", "access_token",
    "refresh_token", "private_key", "session_key",
    "cookie",
)

# Free-form value regex patterns. Order matters — more specific first.
# Captures group 1 if present (for "Bearer <tok>" we only mask the token).
_VALUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key",  re.compile(r"sk-ant-[A-Za-z0-9_\-]{80,}")),
    ("openai_key",     re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{40,}")),
    ("neudrive_token", re.compile(r"ndt_[a-f0-9]{40}")),
    ("github_pat",     re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github_oauth",   re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("github_modern",  re.compile(r"github_pat_[A-Za-z0-9_]{82}")),
    ("aws_akid",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token",    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    # BigModel / GLM API key: 32 hex + dot + 16 alnum (e.g. ace80ee...D61w...)
    ("bigmodel_glm",   re.compile(r"[a-f0-9]{32}\.[A-Za-z0-9]{16}")),
    # Authorization header with Bearer prefix — we only mask the token
    ("bearer_token",   re.compile(r"(?i)(?<=Bearer\s)([A-Za-z0-9._\-+/=]{20,})")),
    # PEM-encoded private keys — mask the whole block
    ("pem_private_key", re.compile(
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        r"[\s\S]*?"
        r"-----END (?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    )),
    # Generic high-entropy long token as last resort — intentionally
    # conservative to avoid false positives. Requires a recognizable
    # prefix separator to minimize matches inside prose.
    # (intentionally omitted — too many false positives)
]

# Minimum plaintext length before a sensitive-keyed value gets masked
# (anything shorter is treated as a placeholder like `""` or `null`).
_MIN_SECRET_LEN = 8


# ── Public API ───────────────────────────────────────────────────────


@dataclass
class RedactionFinding:
    """One scrubbed secret. Does NOT carry the plaintext — only prefix + path."""
    path: str              # JSON-pointer-ish, e.g. "mcp_endpoints[0].headers.Authorization"
    kind: str              # "keyed" | "anthropic_key" | "bearer_token" | ...
    sha256_prefix: str     # first 12 chars of sha256 (for dedupe + audit)
    env_var: str           # suggested replacement identifier, e.g. CC_MCP_WEB_SEARCH_AUTHORIZATION
    placeholder: str       # the string we substituted in (e.g. "${CC_MCP_...}")


def redact(
    obj: Any,
    path: str = "",
    findings: list[RedactionFinding] | None = None,
) -> tuple[Any, list[RedactionFinding]]:
    """Deep-copy `obj` with sensitive values redacted.

    Returns (redacted_copy, findings). Does NOT mutate the input.
    Safe to call on scanner dicts, IR to_dict() output, or nested JSON.
    """
    if findings is None:
        findings = []
    return _walk(obj, path, findings), findings


def to_manifest(findings: list[RedactionFinding]) -> list[dict[str, Any]]:
    """Convert findings to a JSON-safe list (no raw values anywhere).

    Suitable to write as `secrets-manifest.json` next to ir.json.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        key = (f.kind, f.sha256_prefix)
        if key in seen:
            continue
        seen.add(key)
        out.append(asdict(f))
    return out


# ── Internals ────────────────────────────────────────────────────────


def _sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:12]


def _env_var_from_path(path: str) -> str:
    """Derive a stable env-var name from the JSON path."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_").upper()
    return f"CC_{s}" if s else "CC_REDACTED"


def _looks_sensitive_key(key: str) -> bool:
    k = (key or "").lower()
    return any(tok in k for tok in _SENSITIVE_KEY_SUBSTRINGS)


def _mask_keyed(value: str, path: str, findings: list[RedactionFinding]) -> str:
    """Dict key matched sensitive list — mask entire value (preserve Bearer prefix)."""
    if len(value) < _MIN_SECRET_LEN:
        return value  # placeholder / empty — leave alone
    # Skip if value already looks like an env-var reference
    if value.strip().startswith("${") and value.strip().endswith("}"):
        return value
    env_var = _env_var_from_path(path)
    placeholder = "${" + env_var + "}"
    # Preserve "Bearer " prefix if present
    m = re.match(r"(?i)^(Bearer\s+)(.+)$", value)
    if m:
        raw = m.group(2).strip()
        # Already a placeholder (e.g. "Bearer ${CC_X}") — leave alone
        if raw.startswith("${") and raw.endswith("}"):
            return value
        findings.append(RedactionFinding(
            path=path,
            kind="keyed_bearer",
            sha256_prefix=_sha12(raw),
            env_var=env_var,
            placeholder=f"Bearer {placeholder}",
        ))
        return f"Bearer {placeholder}"
    findings.append(RedactionFinding(
        path=path,
        kind="keyed",
        sha256_prefix=_sha12(value),
        env_var=env_var,
        placeholder=placeholder,
    ))
    return placeholder


def _mask_free_form(text: str, path: str, findings: list[RedactionFinding]) -> str:
    """Scan a free-form string for known secret patterns; mask hits in place."""
    out = text
    for kind, pat in _VALUE_PATTERNS:
        def _repl(m: re.Match, _kind: str = kind) -> str:
            raw = m.group(1) if m.groups() else m.group(0)
            env_var = _env_var_from_path(f"{path}.{_kind}")
            placeholder = "${" + env_var + "}"
            findings.append(RedactionFinding(
                path=path,
                kind=_kind,
                sha256_prefix=_sha12(raw),
                env_var=env_var,
                placeholder=placeholder,
            ))
            # If the match includes surrounding context (e.g. via lookbehind), we
            # just replaced the capture group; otherwise replace the whole match.
            if m.groups():
                return m.group(0).replace(raw, placeholder)
            return placeholder
        out = pat.sub(_repl, out)
    return out


def _walk(obj: Any, path: str, findings: list[RedactionFinding]) -> Any:
    if isinstance(obj, dict):
        redacted: dict[str, Any] = {}
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and _looks_sensitive_key(str(k)):
                redacted[k] = _mask_keyed(v, sub, findings)
            else:
                redacted[k] = _walk(v, sub, findings)
        return redacted
    if isinstance(obj, list):
        return [_walk(v, f"{path}[{i}]", findings) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        # Free-form scan applies to every string regardless of key.
        # Short strings skipped to avoid pointless work.
        if len(obj) < 20:
            return obj
        return _mask_free_form(obj, path, findings)
    return obj


__all__ = ["RedactionFinding", "redact", "to_manifest"]

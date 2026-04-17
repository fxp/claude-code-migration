"""Secret detection across scan outputs.

Detects embedded API keys/tokens so adapters can route them to vaults
or env var references instead of writing plaintext.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import Any


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai",      re.compile(r"sk-[A-Za-z0-9]{40,}")),
    ("anthropic",   re.compile(r"sk-ant-[A-Za-z0-9_\-]{80,}")),
    ("neudrive",    re.compile(r"ndt_[a-f0-9]{40}")),
    ("github_pat",  re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github_oauth",re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("slack",       re.compile(r"xox[baprs]-[A-Za-z0-9-]+")),
    ("bigmodel_glm",re.compile(r"[a-f0-9]{32}\.[A-Za-z0-9]{16}")),
]


@dataclass
class SecretFinding:
    source: str
    kind: str
    sha256_prefix: str
    suggested_env_var: str
    raw_value: str


def _sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _env_var_from_source(source: str) -> str:
    return "CC_" + re.sub(r"[^A-Za-z0-9]+", "_", source.upper()).strip("_")


def _classify(value: str) -> str:
    for kind, pat in SECRET_PATTERNS:
        if pat.search(value):
            return kind
    return "opaque"


def scan_secrets(scan_dict: dict[str, Any]) -> list[SecretFinding]:
    """Walk a scan dict and extract secret findings."""
    findings: list[SecretFinding] = []
    seen: set[str] = set()

    def add(source: str, value: str) -> None:
        if not isinstance(value, str) or len(value) < 10:
            return
        h = _sha12(value)
        key = f"{source}:{h}"
        if key in seen:
            return
        seen.add(key)
        findings.append(SecretFinding(
            source=source,
            kind=_classify(value),
            sha256_prefix=h,
            suggested_env_var=_env_var_from_source(source),
            raw_value=value,
        ))

    # MCP server secrets
    for scope in ("mcp_servers_global", "mcp_servers_project"):
        servers = scan_dict.get(scope) or {}
        for name, srv in servers.items():
            if not isinstance(srv, dict):
                continue
            for k, v in (srv.get("headers") or {}).items():
                # Strip optional "Bearer " prefix
                m = re.search(r"Bearer\s+([A-Za-z0-9._\-]{8,})", str(v) or "", re.IGNORECASE)
                if m:
                    add(f"{scope}.{name}.headers.{k}", m.group(1))
                elif "auth" in k.lower() or "token" in k.lower():
                    add(f"{scope}.{name}.headers.{k}", str(v))
            for k, v in (srv.get("env") or {}).items():
                if any(x in k.lower() for x in ("key", "secret", "token", "password")):
                    add(f"{scope}.{name}.env.{k}", str(v))

    # settings.local.json allow rules
    def _scan_allow(label: str, settings: dict[str, Any]) -> None:
        allow = (settings.get("permissions") or {}).get("allow") or []
        for rule in allow:
            if not isinstance(rule, str):
                continue
            for kind, pat in SECRET_PATTERNS:
                for m in pat.findall(rule):
                    value = m if isinstance(m, str) else m[0]
                    add(f"{label}.allow.[{kind}]", value)

    _scan_allow("settings_local", scan_dict.get("settings_local") or {})
    _scan_allow("settings_project_local", scan_dict.get("settings_project_local") or {})

    return findings

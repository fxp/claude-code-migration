"""Tests for src/claude_code_migration/redactor.py.

These lock in the CRITICAL-severity guarantees:
  C1  MCP Bearer tokens never survive to disk
  C2  Free-form pasted secrets in history / session messages are scrubbed
  C3  shell-snapshots / session-envs / file-history scanned recursively
Also verifies: in-memory inputs aren't mutated, innocuous prose is left alone,
env-var placeholders are stable / deterministic.
"""
from __future__ import annotations

import copy
import json

from claude_code_migration.redactor import redact, to_manifest


# ── Fixtures ─────────────────────────────────────────────────────────

_BIGMODEL = "ace80ee67ed349e6a970f823eb99eb84.D61wlpX5wmZPh9LW"
_ANTHROPIC = "sk-ant-" + "a" * 95
_GITHUB_PAT = "ghp_" + "A" * 36
_AWS_AKID = "AKIAIOSFODNN7EXAMPLE"
_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
    "-----END PRIVATE KEY-----"
)


# ── C1 · MCP headers never leak ─────────────────────────────────────

def test_bearer_token_in_mcp_header_is_redacted():
    d = {"mcp_endpoints": [{"headers": {"Authorization": f"Bearer {_BIGMODEL}"}}]}
    out, findings = redact(d)
    assert _BIGMODEL not in json.dumps(out)
    assert out["mcp_endpoints"][0]["headers"]["Authorization"].startswith("Bearer ${CC_")
    # Finding recorded
    assert any(f.kind == "keyed_bearer" for f in findings)


def test_sensitive_env_key_is_redacted():
    d = {"env": {"BIGMODEL_API_KEY": _BIGMODEL, "TOKEN": _GITHUB_PAT}}
    out, _ = redact(d)
    assert out["env"]["BIGMODEL_API_KEY"].startswith("${CC_")
    assert out["env"]["TOKEN"].startswith("${CC_")
    assert _BIGMODEL not in json.dumps(out)
    assert _GITHUB_PAT not in json.dumps(out)


# ── C2 · history.jsonl / chat messages with pasted secrets ──────────

def test_pasted_bigmodel_key_in_history_display_redacted():
    # Real-world shape: history line has a "display" text field, user pasted
    # a full command including the key. No sensitive dict-key name → must be
    # caught by free-form regex, not key-name rule.
    d = {"raw_archive": {"history": [
        {"display": f"help me use BASE_URL:https://api.example KEY:{_BIGMODEL} please"}
    ]}}
    out, findings = redact(d)
    assert _BIGMODEL not in json.dumps(out)
    assert "${CC_" in out["raw_archive"]["history"][0]["display"]
    assert any(f.kind == "bigmodel_glm" for f in findings)


def test_pasted_anthropic_key_in_chat_message():
    d = {"conversations": [{"messages": [
        {"role": "user", "content": f"I have an API key {_ANTHROPIC} — use it for testing"}
    ]}]}
    out, findings = redact(d)
    assert _ANTHROPIC not in json.dumps(out)
    assert any(f.kind == "anthropic_key" for f in findings)


# ── C3 · shell snapshots / envs / file history ──────────────────────

def test_shell_snapshot_export_lines_are_redacted():
    snapshot_body = (
        "export PATH=/usr/local/bin:$PATH\n"
        f"export ANTHROPIC_API_KEY={_ANTHROPIC}\n"
        f"export GITHUB_PAT={_GITHUB_PAT}\n"
        f"export AWS_ACCESS_KEY_ID={_AWS_AKID}\n"
    )
    d = {"raw_archive": {"shell_snapshots": [{"content": snapshot_body}]}}
    out, findings = redact(d)
    assert _ANTHROPIC not in json.dumps(out)
    assert _GITHUB_PAT not in json.dumps(out)
    assert _AWS_AKID not in json.dumps(out)
    kinds = {f.kind for f in findings}
    assert "anthropic_key" in kinds
    assert "github_pat" in kinds
    assert "aws_akid" in kinds


def test_pem_private_key_scrubbed_whole_block():
    d = {"raw_archive": {"file_history": [{"content": f"some text before\n{_PEM}\nafter"}]}}
    out, findings = redact(d)
    blob = out["raw_archive"]["file_history"][0]["content"]
    assert "BEGIN PRIVATE KEY" not in blob
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7" not in blob
    assert any(f.kind == "pem_private_key" for f in findings)


# ── Safety & determinism ─────────────────────────────────────────────

def test_input_is_not_mutated():
    d = {"headers": {"Authorization": f"Bearer {_BIGMODEL}"}}
    snapshot = copy.deepcopy(d)
    redact(d)
    assert d == snapshot, "redactor must not mutate its input"


def test_innocuous_prose_is_untouched():
    d = {
        "description": "This is just normal prose with no secrets in it at all.",
        "count": 42,
        "flag": True,
        "tags": ["alpha", "beta", "gamma"],
    }
    out, findings = redact(d)
    assert out == d
    assert findings == []


def test_short_strings_with_sensitive_key_left_alone():
    # "" or null-ish values should not cause false findings
    d = {"token": "", "api_key": "foo"}  # both < 8 chars after trim
    out, findings = redact(d)
    assert out == d
    assert findings == []


def test_existing_env_var_placeholder_not_double_redacted():
    d = {"headers": {"Authorization": "Bearer ${CC_MCP_X}"}}
    out, _ = redact(d)
    # Already-redacted placeholders must be idempotent
    assert out["headers"]["Authorization"] == "Bearer ${CC_MCP_X}"


def test_manifest_dedupes_repeated_findings():
    # Same secret appearing at multiple paths should produce a concise manifest
    d = {
        "a": {"authorization": f"Bearer {_BIGMODEL}"},
        "b": {"token": _BIGMODEL},
        "c": f"prose mentioning the key {_BIGMODEL} twice — {_BIGMODEL}",
    }
    _, findings = redact(d)
    manifest = to_manifest(findings)
    # Distinct by (kind, sha256_prefix) — same raw secret with same kind merges
    keys = {(m["kind"], m["sha256_prefix"]) for m in manifest}
    assert len(manifest) == len(keys)


def test_env_var_names_are_deterministic():
    # Same input twice → same env var names (no hash-based instability)
    d = {"mcp": {"headers": {"Authorization": f"Bearer {_BIGMODEL}"}}}
    _, f1 = redact(d)
    _, f2 = redact(d)
    assert [f.env_var for f in f1] == [f.env_var for f in f2]
    assert f1[0].env_var.startswith("CC_")

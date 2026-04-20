"""Tests for the redactor middleware.

Smoke-tests the wrapper; the underlying ccm.redactor has its own test
suite in claude-code-migration.
"""
from __future__ import annotations

from claude_code_migration.hub.redact import Redactor, scrub_one


_BIGMODEL = "ace80ee67ed349e6a970f823eb99eb84.D61wlpX5wmZPh9LW"
_ANTHROPIC = "sk-ant-" + "a" * 95


def test_bearer_mcp_header_redacted():
    r = Redactor()
    payload = {
        "headers": {"Authorization": f"Bearer {_BIGMODEL}"},
    }
    res = r.scrub(payload)
    assert _BIGMODEL not in str(res.scrubbed)
    assert res.has_secrets
    assert r.pending_count == 1


def test_pasted_key_in_prose_redacted():
    res = scrub_one({
        "content_text": f"hey team, my key is {_ANTHROPIC} so please take care"
    })
    assert _ANTHROPIC not in res.scrubbed["content_text"]
    assert res.has_secrets


def test_innocuous_prose_untouched():
    r = Redactor()
    res = r.scrub({"content_text": "just a normal conversation"})
    assert not res.has_secrets
    assert r.pending_count == 0
    assert res.scrubbed["content_text"] == "just a normal conversation"


def test_pending_accumulates_across_scrubs():
    r = Redactor()
    r.scrub({"token": "sk-ant-" + "x" * 95})
    r.scrub({"api_key": "sk-ant-" + "y" * 95})
    assert r.pending_count == 2
    drained = r.drain_vault_candidates()
    assert len(drained) == 2
    assert r.pending_count == 0


def test_drain_empties_state():
    r = Redactor()
    r.scrub({"bearer": "sk-ant-" + "z" * 95})
    assert r.drain_vault_candidates()
    assert not r.drain_vault_candidates()   # second drain is empty

"""Integration tests for hub.py against a local mock HTTP server.

Covers the gap left by tests/test_hardening.py (which only exercises the
client-side path-traversal guard). Here we verify that every hub method
sends the right verb / path / payload, and that `push_scan_to_hub()`
correctly maps a scan dict + cowork export onto neuDrive's canonical
paths (profile / tree / import/skill / vault).

We do NOT hit live neuDrive. The mock just replies with `{ok:true,data:{}}`
(the envelope the real API uses) and records every received request.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from claude_code_migration.neudrive import NeuDriveHub, push_scan_to_hub


# ── Mock server ──────────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []


@pytest.fixture
def mock_hub():
    """Yields a tuple (NeuDriveHub, recorder) pointing at an ephemeral HTTP server.

    The server echoes {"ok":true,"data":{}} for every request (and a fake
    user+scopes for /agent/auth/whoami), and records each call so tests can
    assert on verbs / paths / bodies / auth header.
    """
    recorder = _Recorder()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _handle(self):
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(ln) if ln else b""
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = raw.decode("utf-8", errors="replace")
            recorder.calls.append({
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "body": body,
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path.endswith("/agent/auth/whoami"):
                self.wfile.write(b'{"ok":true,"data":{"user":"t","scopes":["write:tree"]}}')
            else:
                self.wfile.write(b'{"ok":true,"data":{}}')

        do_GET = _handle
        do_PUT = _handle
        do_POST = _handle

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        hub = NeuDriveHub(base_url=f"http://127.0.0.1:{port}", token="ndt_" + "0" * 40)
        yield hub, recorder
        hub.close()
    finally:
        srv.shutdown()


# ── Individual endpoint coverage ─────────────────────────────────────

def test_whoami_uses_bearer_auth(mock_hub):
    hub, rec = mock_hub
    info = hub.whoami()
    assert info["user"] == "t"
    assert rec.calls[-1]["method"] == "GET"
    assert rec.calls[-1]["path"] == "/agent/auth/whoami"
    assert rec.calls[-1]["auth"].startswith("Bearer ndt_")


def test_update_profile_sends_category_and_content(mock_hub):
    hub, rec = mock_hub
    hub.update_profile("principles", "be concise")
    call = rec.calls[-1]
    assert call["method"] == "PUT"
    assert call["path"] == "/agent/memory/profile"
    assert call["body"] == {"category": "principles", "content": "be concise"}


def test_write_file_wraps_path_under_agent_tree(mock_hub):
    hub, rec = mock_hub
    hub.write_file("/memory/scratch/2026-04-19/notes.md", "hello")
    call = rec.calls[-1]
    assert call["method"] == "PUT"
    assert call["path"] == "/agent/tree/memory/scratch/2026-04-19/notes.md"
    assert call["body"] == {"content": "hello"}


def test_write_file_prepends_leading_slash_when_missing(mock_hub):
    hub, rec = mock_hub
    hub.write_file("memory/scratch/x.md", "x")  # no leading /
    assert rec.calls[-1]["path"] == "/agent/tree/memory/scratch/x.md"


def test_import_claude_memory_posts_bulk(mock_hub):
    hub, rec = mock_hub
    hub.import_claude_memory([{"file": "a.md", "content": "x"}])
    call = rec.calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/agent/import/claude-memory"
    assert call["body"] == {"memories": [{"file": "a.md", "content": "x"}]}


def test_import_skill_posts_name_and_files(mock_hub):
    hub, rec = mock_hub
    hub.import_skill("cc-sample", {"SKILL.md": "# body"})
    call = rec.calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/agent/import/skill"
    assert call["body"] == {"name": "cc-sample", "files": {"SKILL.md": "# body"}}


def test_write_secret_puts_under_vault_scope(mock_hub):
    hub, rec = mock_hub
    hub.write_secret("claude/web-search/token", "sk-xxx")
    call = rec.calls[-1]
    assert call["method"] == "PUT"
    assert call["path"] == "/agent/vault/claude/web-search/token"
    assert call["body"] == {"data": "sk-xxx"}


# ── push_scan_to_hub mapping ─────────────────────────────────────────

def test_push_scan_profile_and_memory_routing(mock_hub):
    hub, rec = mock_hub
    scan = {
        "home_claude_md": "# user profile\nbe concise",
        "memory": [
            {"type": "user",    "content": "I like tests", "file": "user.md"},
            {"type": "project", "content": "proj ctx",     "file": "proj.md"},
            {"type": "feedback","content": "run tests",    "file": "fb.md"},
            {"type": "scratch", "content": "temp",         "file": "t.md"},  # skipped
        ],
        "skills_global": [
            {"name": "skA", "body": "# skA"},
            {"name": "skB", "body": "# skB"},
        ],
    }
    stats = push_scan_to_hub(scan, hub)

    # Stats: 2 profile entries (principles + preferences),
    #        2 memory files (project + feedback — scratch excluded),
    #        2 skills
    assert stats["profile_entries"] == 2
    assert stats["memory_files"] == 2
    assert stats["skills_uploaded"] == 2

    # Verify paths actually used
    paths = [c["path"] for c in rec.calls]
    assert "/agent/memory/profile" in paths  # principles
    assert any(p == "/agent/tree/memory/scratch/" + __import__("datetime").datetime
               .now().strftime("%Y-%m-%d") + "/cc-proj.md" for p in paths)
    assert "/agent/import/skill" in paths


def test_push_scan_cowork_conversations_land_under_canonical_path(mock_hub):
    hub, rec = mock_hub
    cowork = {
        "source": "claude-chat",
        "conversations": [
            {"uuid": "abc12345-full-uuid-here", "name": "hello",
             "messages": [
                 {"sender": "human",     "timestamp": "2026-04-19", "text": "hi"},
                 {"sender": "assistant", "timestamp": "2026-04-19", "text": "hey"},
             ]},
        ],
    }
    stats = push_scan_to_hub({}, hub, cowork_export=cowork)
    assert stats["conversations_uploaded"] == 1

    tree_put = [c for c in rec.calls if c["path"].startswith("/agent/tree/conversations/")]
    assert len(tree_put) == 1
    # Exact canonical path: /conversations/<platform>/<uuid-8-prefix>/conversation.md
    assert tree_put[0]["path"] == (
        "/agent/tree/conversations/claude-chat/abc12345/conversation.md"
    )
    # Body contains rendered markdown with role + timestamp headers
    body = tree_put[0]["body"]["content"]
    assert "# hello" in body
    assert "## human" in body
    assert "## assistant" in body


def test_push_scan_survives_skill_error_and_reports_it(mock_hub, capsys):
    """If one skill upload fails, the others should still proceed + stderr summary."""
    import httpx
    hub, _ = mock_hub

    # Make import_skill raise for one name only
    orig = hub.import_skill
    def flaky(name, files):
        if name == "cc-bad":
            req = httpx.Request("POST", "/agent/import/skill")
            raise httpx.HTTPStatusError("500", request=req,
                                        response=httpx.Response(500, request=req))
        return orig(name, files)
    hub.import_skill = flaky  # type: ignore

    scan = {
        "skills_global": [
            {"name": "ok-1",  "body": "ok body"},
            {"name": "bad",   "body": "will fail"},
            {"name": "ok-2",  "body": "ok body 2"},
        ],
    }
    stats = push_scan_to_hub(scan, hub)
    # 2 succeeded + 1 errored — recorded in stats, error printed to stderr
    assert stats["skills_uploaded"] == 2
    assert stats.get("errors") == 1
    err = capsys.readouterr().err
    assert "hub push error" in err
    assert "bad" in err

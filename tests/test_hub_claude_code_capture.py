"""Tests for ClaudeCodeFSCapture — the real-time tailer.

Builds a synthetic ~/.claude/projects/<enc>/<session>.jsonl tree under
a temp dir and verifies:

  • Initial start() catches up on existing content.
  • Offset persisted so daemon restart doesn't duplicate messages.
  • Incremental appends are streamed (we tail directly rather than wait
    on fsnotify — that part is tested via on-modified hook).
  • File truncation resets the offset.
  • Message rows land with correct source_uuid / role / content_text.
  • A Conversation row is emitted once per session.
  • Parse errors from partial writes are tolerated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_code_migration.hub.buffer import LocalBuffer
from claude_code_migration.hub.captures import ClaudeCodeFSCapture
from claude_code_migration.hub.captures.base import CaptureContext
from claude_code_migration.hub.redact import Redactor


def _mk_record(uuid: str, role: str, text: str, ts: str = "2026-04-20T00:00:00Z") -> dict:
    return {
        "uuid": uuid,
        "timestamp": ts,
        "type": role,
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
        },
    }


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _emitted_targets(buf: LocalBuffer) -> list[str]:
    rows = buf._conn.execute("select target from outbox order by id asc").fetchall()
    return [r["target"] for r in rows]


def _emitted_payloads(buf: LocalBuffer, target: str) -> list[dict]:
    rows = buf._conn.execute(
        "select payload from outbox where target = ? order by id asc", (target,)
    ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


@pytest.fixture
def claude_tree(tmp_path):
    home = tmp_path / "fake-claude"
    (home / "projects" / "-proj").mkdir(parents=True)
    return home


@pytest.fixture
def ctx(tmp_path):
    buf = LocalBuffer(tmp_path / "buffer.db")
    yield CaptureContext(
        buffer=buf,
        redactor=Redactor(),
        source_platform="claude-code",
    )
    buf.close()


def test_catches_up_on_existing_jsonl(ctx, claude_tree):
    sess = claude_tree / "projects" / "-proj" / "session-abc.jsonl"
    _append(sess, _mk_record("m1", "user", "hello"))
    _append(sess, _mk_record("m2", "assistant", "hi"))

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()

    # 1 project + 1 conversation + 2 messages + some audit logs
    targets = _emitted_targets(ctx.buffer)
    assert "dossier_projects" in targets
    assert "dossier_conversations" in targets
    assert targets.count("dossier_messages") == 2


def test_message_rows_carry_role_and_text(ctx, claude_tree):
    sess = claude_tree / "projects" / "-proj" / "session-xyz.jsonl"
    _append(sess, _mk_record("m1", "user", "what time is it"))

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()

    msgs = _emitted_payloads(ctx.buffer, "dossier_messages")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content_text"] == "what time is it"
    assert msgs[0]["source_uuid"] == "m1"
    assert msgs[0]["_conversation_source_uuid"] == "session-xyz"


def test_conversation_emitted_once_per_session(ctx, claude_tree):
    sess = claude_tree / "projects" / "-proj" / "session-onceonly.jsonl"
    for i in range(5):
        _append(sess, _mk_record(f"m{i}", "user", f"msg {i}"))

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()

    conv = _emitted_payloads(ctx.buffer, "dossier_conversations")
    assert len(conv) == 1
    assert conv[0]["source_uuid"] == "session-onceonly"


def test_offset_persists_across_restarts(ctx, claude_tree):
    sess = claude_tree / "projects" / "-proj" / "session-persist.jsonl"
    _append(sess, _mk_record("m1", "user", "first"))

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()
    first_msg_count = len(_emitted_payloads(ctx.buffer, "dossier_messages"))

    # Append more, then restart capture
    _append(sess, _mk_record("m2", "assistant", "second"))

    cap2 = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap2.start()
    cap2.stop()

    second_msg_count = len(_emitted_payloads(ctx.buffer, "dossier_messages"))
    # Only one additional message should have been emitted.
    assert second_msg_count == first_msg_count + 1


def test_truncated_file_rescans_from_zero(ctx, claude_tree):
    sess = claude_tree / "projects" / "-proj" / "session-trunc.jsonl"
    _append(sess, _mk_record("m1", "user", "one"))
    _append(sess, _mk_record("m2", "user", "two"))

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()
    assert len(_emitted_payloads(ctx.buffer, "dossier_messages")) == 2

    # Truncate the file — simulate log rotation
    sess.write_text("")
    _append(sess, _mk_record("m3", "user", "fresh"))

    cap2 = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap2.start()
    cap2.stop()

    all_msgs = _emitted_payloads(ctx.buffer, "dossier_messages")
    # 2 from pre-truncation + 1 fresh
    uuids = [m["source_uuid"] for m in all_msgs]
    assert "m3" in uuids


def test_partial_json_line_is_tolerated(ctx, claude_tree):
    """If a write is mid-flight and we catch half a line, skip it gracefully."""
    sess = claude_tree / "projects" / "-proj" / "session-partial.jsonl"
    _append(sess, _mk_record("m1", "user", "complete"))
    # Add a partial line (no newline, broken JSON)
    with sess.open("a") as f:
        f.write('{"uuid": "mX", "message": {"role": "us')

    cap = ClaudeCodeFSCapture(ctx, claude_home=claude_tree)
    cap.start()
    cap.stop()

    msgs = _emitted_payloads(ctx.buffer, "dossier_messages")
    # Only the complete record made it through
    assert [m["source_uuid"] for m in msgs] == ["m1"]


def test_missing_projects_dir_is_handled_gracefully(ctx, tmp_path):
    # No ~/.claude/ at all
    cap = ClaudeCodeFSCapture(ctx, claude_home=tmp_path / "nonexistent")
    cap.start()
    cap.stop()
    # No crash, no rows emitted
    assert ctx.buffer.outbox_size() == 0

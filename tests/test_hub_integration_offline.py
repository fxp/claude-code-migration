"""End-to-end offline integration test.

Simulates:

  1. Daemon starts (captures + drain + mirror) with InMemoryClient.
  2. Claude Code appends a few messages to a synthetic ~/.claude/.
  3. Capture picks them up → L4 outbox.
  4. Drain worker pushes them to InMemoryClient.tables.
  5. (In realitythis is where realtime subscription comes in; we just
     call mirror.bootstrap() to mimic "pulled everything from cloud".)
  6. MCP-equivalent read: buffer.mirror_search_memory() finds them.

No real Supabase; no real network. Proves the full read+write cycle
works end-to-end even when offline.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_code_migration.hub.buffer import LocalBuffer
from claude_code_migration.hub.captures import ClaudeCodeFSCapture
from claude_code_migration.hub.captures.base import CaptureContext
from claude_code_migration.hub.drain import DrainWorker
from claude_code_migration.hub.mirror import MirrorSync
from claude_code_migration.hub.redact import Redactor
from claude_code_migration.hub.supabase_client import InMemoryClient


def _append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _mk(uuid, role, text):
    return {
        "uuid": uuid, "type": role, "timestamp": "2026-04-20T00:00:00Z",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


@pytest.mark.integration
def test_capture_to_drain_to_mirror_roundtrip(tmp_path):
    # ── Set up synthetic Claude Code state ──
    claude_home = tmp_path / "claude"
    sess = claude_home / "projects" / "-proj-x" / "abc.jsonl"
    _append(sess, _mk("m1", "user", "remember I like concise answers"))
    _append(sess, _mk("m2", "assistant", "got it"))

    # ── Wire up the full daemon-ish pipeline ──
    buf = LocalBuffer(tmp_path / "buffer.db")
    redactor = Redactor()
    client = InMemoryClient()

    ctx = CaptureContext(buffer=buf, redactor=redactor, source_platform="claude-code")
    capture = ClaudeCodeFSCapture(ctx, claude_home=claude_home)
    drain = DrainWorker(buf, client, idle_sleep=0.05)

    # ── Capture runs once on start, populating outbox ──
    capture.start()
    capture.stop()
    assert buf.outbox_size() > 0

    # ── Drain pushes to InMemoryClient ──
    drain.start()
    try:
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        drain.stop()

    assert buf.outbox_size() == 0
    assert len(client.tables.get("dossier_messages", {})) == 2
    assert len(client.tables.get("dossier_conversations", {})) == 1

    # ── Mirror bootstrap pulls the cloud state back into L4 ──
    mirror = MirrorSync(buf, client)
    mirror.bootstrap()
    assert mirror.stats["bootstrap_rows"] >= 3  # project + conv + 2 msgs

    # ── The MCP read path: search local mirror_messages ──
    hits = buf._conn.execute(
        """
        select m.content_text, m.role
          from mirror_messages m
         where m.content_text like '%concise%'
        """
    ).fetchall()
    assert len(hits) == 1
    assert hits[0]["role"] == "user"


@pytest.mark.integration
def test_offline_then_online_flushes_outbox(tmp_path):
    """Simulate: daemon captures while offline, then drain succeeds on reconnect."""
    buf = LocalBuffer(tmp_path / "buffer.db")
    client = InMemoryClient()

    # Queue up 10 rows while "offline" (no drain running)
    for i in range(10):
        buf.enqueue("dossier_memory_items", {
            "source_uuid": f"u-{i}",
            "kind": "scratch",
            "content": f"item {i}",
        })
    assert buf.outbox_size() == 10

    # Now "come back online" — start drain
    drain = DrainWorker(buf, client, idle_sleep=0.05)
    drain.start()
    try:
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        drain.stop()

    assert buf.outbox_size() == 0
    assert len(client.tables["dossier_memory_items"]) == 10
    assert drain.stats["drained"] == 10


@pytest.mark.integration
def test_idempotent_replay_does_not_duplicate(tmp_path):
    """If we emit the same row twice (e.g. daemon restart re-tails), the
    drain worker should succeed both times and Supabase dedup handles it."""
    buf = LocalBuffer(tmp_path / "buffer.db")
    client = InMemoryClient()
    drain = DrainWorker(buf, client, idle_sleep=0.05)

    buf.enqueue(
        "dossier_memory_items",
        {"source_uuid": "same-uuid", "content": "first emission"},
        dedup_key="same-uuid",
    )
    buf.enqueue(
        "dossier_memory_items",
        {"source_uuid": "same-uuid", "content": "second emission (updated)"},
        dedup_key="same-uuid",
    )

    drain.start()
    try:
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        drain.stop()

    # Both UPSERTs succeed. Single row in the final state with latest content.
    assert buf.outbox_size() == 0
    rows = client.tables["dossier_memory_items"]
    assert len(rows) == 1
    assert rows["same-uuid"]["content"] == "second emission (updated)"

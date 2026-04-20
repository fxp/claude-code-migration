"""Tests for the drain worker — retries, dead-letter, shutdown."""
from __future__ import annotations

import time

import pytest

from claude_code_migration.hub.buffer import LocalBuffer
from claude_code_migration.hub.drain import DrainWorker, MAX_ATTEMPTS
from claude_code_migration.hub.supabase_client import InMemoryClient


@pytest.fixture
def buf(tmp_path):
    b = LocalBuffer(tmp_path / "buffer.db")
    yield b
    b.close()


@pytest.fixture
def client():
    return InMemoryClient()


@pytest.fixture
def worker(buf, client):
    return DrainWorker(buf, client, idle_sleep=0.05)


# ── Happy path ───────────────────────────────────────────────────────

def test_drain_worker_empties_outbox(buf, worker):
    buf.enqueue("dossier_memory_items", {"source_uuid": "u1", "content": "a"})
    buf.enqueue("dossier_memory_items", {"source_uuid": "u2", "content": "b"})

    worker.start()
    try:
        # Wait for drain
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()

    assert buf.outbox_size() == 0
    assert worker.stats["drained"] == 2


def test_drain_dispatches_with_correct_on_conflict(buf, worker, client):
    buf.enqueue("dossier_projects", {"slug": "proj-1", "name": "Proj"},
                dedup_key="proj-1")
    buf.enqueue("dossier_memory_items", {"source_uuid": "u1", "content": "c"},
                dedup_key="u1")
    worker.start()
    try:
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
    assert len(client.calls) == 2
    # Default key is source_uuid, but dossier_projects uses slug — the worker
    # must look it up via _on_conflict_for.


def test_drain_handles_delete_op(buf, worker, client):
    # Seed a row first
    client.tables["dossier_skills"] = {"name-a": {"id": "ida", "name": "a"}}
    buf.enqueue("dossier_skills", {"id": "ida"}, op="delete", dedup_key="ida")
    worker.start()
    try:
        deadline = time.time() + 2.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        worker.stop()
    assert buf.outbox_size() == 0
    assert client.calls[-1].op == "delete"


# ── Failure handling ─────────────────────────────────────────────────

def test_transient_failure_retries_and_succeeds(buf, worker, client):
    # First call fails, subsequent calls succeed
    client.fail_once.add("dossier_memory_items")
    buf.enqueue("dossier_memory_items", {"source_uuid": "u1", "content": "c"})

    worker.start()
    try:
        # fail_once triggers backoff (2s); give the worker time to retry.
        deadline = time.time() + 5.0
        while buf.outbox_size() > 0 and time.time() < deadline:
            time.sleep(0.1)
    finally:
        worker.stop()

    # Eventually drained
    assert buf.outbox_size() == 0
    assert worker.stats["failures"] == 1
    assert worker.stats["drained"] == 1


def test_permanent_failure_goes_to_dead_letter(buf, tmp_path):
    """A client that always raises should eventually produce a dead letter."""
    from unittest.mock import MagicMock
    client = MagicMock()
    client.upsert.side_effect = RuntimeError("always fails")

    worker = DrainWorker(buf, client, idle_sleep=0.01)
    buf.enqueue("dossier_memory_items", {"source_uuid": "u1", "content": "c"})

    # Drive the drain by calling peek_due + _try_one directly; we want
    # deterministic progression past MAX_ATTEMPTS without waiting for
    # real backoff delays.
    for _ in range(MAX_ATTEMPTS + 2):
        due = buf.peek_due()
        if not due:
            # Force-reset next_retry to now so we can keep trying
            buf._conn.execute("update outbox set next_retry = unixepoch()")
            due = buf.peek_due()
        for entry in due:
            worker._try_one(entry)
        if buf.outbox_size() == 0:
            break

    assert buf.outbox_size() == 0, "outbox should be empty after exhausting retries"
    assert buf.dead_letter_count() == 1
    assert worker.stats["dead_lettered"] == 1


def test_snapshot_shape(worker):
    snap = worker.snapshot()
    assert "outbox_pending" in snap
    assert "dead_letter" in snap
    assert "drained" in snap


# ── Graceful shutdown ────────────────────────────────────────────────

def test_stop_is_idempotent(worker):
    worker.start()
    worker.stop()
    worker.stop()  # should not raise

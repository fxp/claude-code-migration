"""Tests for L4 LocalBuffer — outbox, mirror, FTS, housekeeping."""
from __future__ import annotations

import json
import time

import pytest

from claude_code_migration.hub.buffer import LocalBuffer


@pytest.fixture
def buf(tmp_path):
    b = LocalBuffer(tmp_path / "buffer.db")
    yield b
    b.close()


# ── Outbox ───────────────────────────────────────────────────────────

def test_enqueue_returns_id_and_size_tracks(buf):
    assert buf.outbox_size() == 0
    rid = buf.enqueue("dossier_memory_items", {"name": "hi", "content": "x"})
    assert rid > 0
    assert buf.outbox_size() == 1


def test_peek_due_returns_serialized_payload(buf):
    buf.enqueue("dossier_messages", {"role": "user", "content_text": "hi"},
                dedup_key="uuid-1")
    entries = buf.peek_due()
    assert len(entries) == 1
    e = entries[0]
    assert e.target == "dossier_messages"
    assert e.op == "upsert"
    assert e.payload["role"] == "user"
    assert e.dedup_key == "uuid-1"
    assert e.attempts == 0


def test_mark_done_removes_row(buf):
    rid = buf.enqueue("t", {"a": 1})
    buf.mark_done(rid)
    assert buf.outbox_size() == 0


def test_mark_failed_schedules_backoff(buf):
    rid = buf.enqueue("t", {"a": 1})
    buf.mark_failed(rid, "network down")
    # After mark_failed, row isn't due yet
    entries = buf.peek_due()
    assert entries == []
    # But the entry is still in the outbox with attempts=1
    assert buf.outbox_size() == 1


def test_give_up_moves_to_dead_letter(buf):
    rid = buf.enqueue("t", {"a": 1})
    buf.give_up(rid, reason="max attempts")
    assert buf.outbox_size() == 0
    assert buf.dead_letter_count() == 1


def test_peek_respects_next_retry_ordering(buf):
    buf.enqueue("a", {"x": 1})
    buf.enqueue("b", {"y": 2})
    # Both due immediately
    due = buf.peek_due()
    assert len(due) == 2
    assert due[0].target == "a"  # FIFO


# ── Mirror ───────────────────────────────────────────────────────────

def test_mirror_upsert_memory_then_search(buf):
    buf.mirror_upsert("mirror_memory_items", {
        "id": "uuid-1",
        "kind": "user_profile",
        "name": "preferences",
        "content": "I prefer concise answers and always run tests.",
    })
    buf.mirror_upsert("mirror_memory_items", {
        "id": "uuid-2",
        "kind": "project",
        "name": "widget",
        "content": "We build widgets in React.",
    })
    hits = buf.mirror_search_memory("concise")
    assert len(hits) == 1
    assert hits[0]["id"] == "uuid-1"
    assert "<b>concise</b>" in hits[0]["snippet"]


def test_mirror_search_scoped_by_kind(buf):
    buf.mirror_upsert("mirror_memory_items",
                      {"id": "u1", "kind": "user_profile", "name": "x",
                       "content": "concise answers"})
    buf.mirror_upsert("mirror_memory_items",
                      {"id": "u2", "kind": "project", "name": "y",
                       "content": "concise answers"})
    hits_all = buf.mirror_search_memory("concise")
    hits_scoped = buf.mirror_search_memory("concise", kind="project")
    assert len(hits_all) == 2
    assert len(hits_scoped) == 1
    assert hits_scoped[0]["id"] == "u2"


def test_mirror_delete_removes_row(buf):
    buf.mirror_upsert("mirror_skills",
                      {"id": "s1", "name": "browse", "body": "..."})
    assert len(buf.mirror_list_skills()) == 1
    buf.mirror_delete("mirror_skills", "s1")
    assert buf.mirror_list_skills() == []


def test_mirror_list_skills_returns_all(buf):
    for i, name in enumerate(["a", "b", "c"]):
        buf.mirror_upsert("mirror_skills",
                          {"id": f"s{i}", "name": name,
                           "description": f"desc-{name}", "body": "..."})
    skills = buf.mirror_list_skills()
    assert [s["name"] for s in skills] == ["a", "b", "c"]


def test_mirror_read_skill_missing_returns_none(buf):
    assert buf.mirror_read_skill("nonexistent") is None


def test_mirror_rejects_unknown_table(buf):
    with pytest.raises(ValueError, match="unknown mirror table"):
        buf.mirror_upsert("mirror_not_a_thing", {"id": "x"})


# ── Sync state ───────────────────────────────────────────────────────

def test_state_roundtrip(buf):
    assert buf.get_state("foo", "default") == "default"
    buf.set_state("foo", "bar")
    assert buf.get_state("foo") == "bar"
    # overwrite
    buf.set_state("foo", "baz")
    assert buf.get_state("foo") == "baz"


def test_schema_version_initialized(buf):
    assert buf.get_state("schema_version") == "1"


# ── Housekeeping ─────────────────────────────────────────────────────

def test_vacuum_does_not_crash(buf):
    buf.enqueue("t", {"x": 1})
    buf.mirror_upsert("mirror_memory_items",
                      {"id": "u1", "kind": "scratch", "name": "n", "content": "c"})
    buf.vacuum()
    # Still intact
    assert buf.outbox_size() == 1
    assert len(buf.mirror_search_memory("c")) == 1

"""Drain worker — pulls rows out of the L4 outbox and pushes them to the
HubClient (Supabase in prod, InMemory in tests).

Life cycle:
    worker = DrainWorker(buffer, client)
    worker.start()   # background thread
    ...
    worker.stop()    # graceful shutdown; waits up to 5 s

Invariants:
    • Never raises past the worker thread — all errors become `mark_failed`
      on the outbox row with exponential backoff.
    • Attempts ≥ MAX_ATTEMPTS → give_up() moves the row to dead_letter.
    • Idempotency is the client's job (Supabase UPSERT with on_conflict).
      Same row being sent twice is safe.
    • Gracefully handles offline: if the client raises network errors,
      rows stay in the outbox and the worker keeps retrying.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any

from .buffer import LocalBuffer, OutboxEntry
from .supabase_client import HubClient


MAX_ATTEMPTS = 10
IDLE_SLEEP_S = 1.0
BATCH_SIZE = 50
# When a batch has this many consecutive network-ish failures, back off harder.
BURST_FAILURE_THRESHOLD = 5
BURST_BACKOFF_S = 30.0


class DrainWorker:
    def __init__(
        self,
        buffer: LocalBuffer,
        client: HubClient,
        *,
        idle_sleep: float = IDLE_SLEEP_S,
        batch_size: int = BATCH_SIZE,
    ):
        self.buffer = buffer
        self.client = client
        self.idle_sleep = idle_sleep
        self.batch_size = batch_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats: dict[str, int] = {
            "drained": 0,
            "failures": 0,
            "dead_lettered": 0,
            "burst_pauses": 0,
        }

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="dossier-hub-drain", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    # ── Main loop ───────────────────────────────────────────────

    def _run(self) -> None:
        consecutive_fails = 0
        while not self._stop.is_set():
            try:
                batch = self.buffer.peek_due(limit=self.batch_size)
            except Exception as e:
                print(f"[drain] peek_due failed: {e}", file=sys.stderr)
                self._stop.wait(self.idle_sleep)
                continue

            if not batch:
                self._stop.wait(self.idle_sleep)
                consecutive_fails = 0
                continue

            for entry in batch:
                if self._stop.is_set():
                    break
                ok = self._try_one(entry)
                if ok:
                    consecutive_fails = 0
                else:
                    consecutive_fails += 1

            if consecutive_fails >= BURST_FAILURE_THRESHOLD:
                # Likely offline or Supabase is down — back off hard so we
                # don't hammer with useless retries.
                self.stats["burst_pauses"] += 1
                print(
                    f"[drain] {consecutive_fails} consecutive failures — "
                    f"backing off {BURST_BACKOFF_S}s",
                    file=sys.stderr,
                )
                self._stop.wait(BURST_BACKOFF_S)
                consecutive_fails = 0

    def _try_one(self, entry: OutboxEntry) -> bool:
        try:
            self._dispatch(entry)
        except Exception as e:
            self.stats["failures"] += 1
            error = f"{type(e).__name__}: {e}"
            # attempts already incremented by mark_failed below
            if entry.attempts + 1 >= MAX_ATTEMPTS:
                self.stats["dead_lettered"] += 1
                self.buffer.give_up(entry.id, reason=error)
                print(f"[drain] gave up on outbox#{entry.id} "
                      f"({entry.target}) after {entry.attempts} attempts: {error}",
                      file=sys.stderr)
            else:
                self.buffer.mark_failed(entry.id, error)
            return False
        else:
            self.buffer.mark_done(entry.id)
            self.stats["drained"] += 1
            return True

    def _dispatch(self, entry: OutboxEntry) -> None:
        if entry.op == "upsert":
            on_conflict = self._on_conflict_for(entry.target)
            self.client.upsert(entry.target, entry.payload, on_conflict=on_conflict)
        elif entry.op == "delete":
            row_id = entry.payload.get("id") or entry.dedup_key
            if not row_id:
                raise ValueError("delete op has no id/dedup_key")
            self.client.delete(entry.target, row_id)
        elif entry.op == "rpc":
            name = entry.payload.get("name")
            args = entry.payload.get("args") or {}
            if not name:
                raise ValueError("rpc op has no name")
            self.client.rpc(name, args)
        else:
            raise ValueError(f"unknown op: {entry.op}")

    @staticmethod
    def _on_conflict_for(table: str) -> str:
        """The column that maps 1:1 between L4 outbox and Supabase UNIQUE."""
        # Most tables UPSERT on source_uuid. A few use different keys.
        overrides = {
            "dossier_identity": "account_uuid",
            "dossier_projects": "slug",
            "dossier_skills": "name",
            "dossier_agents": "name",
            "dossier_marketplaces": "name",
            "dossier_scheduled_tasks": "name",
            "dossier_plugins": "plugin_id",
            "dossier_vault_entries": "scope",
        }
        return overrides.get(table, "source_uuid")

    # ── Inspection ──────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "outbox_pending": self.buffer.outbox_size(),
            "dead_letter": self.buffer.dead_letter_count(),
            **self.stats,
        }


__all__ = ["DrainWorker", "MAX_ATTEMPTS"]

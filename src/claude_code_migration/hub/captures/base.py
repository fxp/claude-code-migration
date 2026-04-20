"""Base class for all captures.

Design contract:

  Capture subclasses expose start() / stop(). They never touch Supabase
  directly; everything goes through CaptureContext.emit() which does:

      raw dict  →  Redactor.scrub  →  LocalBuffer.enqueue

  If Redactor finds secrets, they're accumulated on `ctx.redactor.pending`
  and later drained into dossier_vault_entries (age-encrypted) by the
  vault worker.

  Captures should be idempotent: if they re-parse the same source twice
  (file restart, daemon reboot, offset lost), the dedup_key + Supabase
  UNIQUE constraint will de-dupe at the hub level.

Subclass responsibilities:

  name:       short identifier ('claude_code_fs', 'claude_desktop_mcp_proxy')
  start():    begin observing. Non-blocking; spawn threads if needed.
  stop():     stop observing cleanly; flush pending writes if any.
  emit(row):  (helper) push one Dossier-shaped row through the pipeline.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

from ..buffer import LocalBuffer
from ..redact import Redactor


@dataclass
class CaptureContext:
    """Shared state passed to every capture at construction time."""
    buffer: LocalBuffer
    redactor: Redactor
    source_platform: str          # e.g. 'claude-code' — stamped onto every row

    def emit(
        self,
        target_table: str,
        row: dict[str, Any],
        *,
        dedup_key: str | None = None,
        capture_source: str = "unknown",
    ) -> int:
        """Scrub + enqueue. Returns the outbox row id (0 on skip).

        `dedup_key` should be the value that matches the target table's
        Supabase UNIQUE constraint (typically source_uuid).
        """
        t0 = time.perf_counter()
        try:
            # Always stamp source_platform on the row if absent.
            row.setdefault("source_platform", self.source_platform)
            # Scrub secrets
            result = self.redactor.scrub(row, source_path=target_table)
            rid = self.buffer.enqueue(
                target_table,
                result.scrubbed,
                dedup_key=dedup_key or result.scrubbed.get("source_uuid"),
            )
            # Audit log entry (not uploaded here; drain worker will later
            # push these as a batch for server-side aggregation)
            self._audit(
                capture_source, target_table, "upsert", dedup_key,
                result="redacted" if result.has_secrets else "ok",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            return rid
        except Exception as e:
            self._audit(
                capture_source, target_table, "upsert", dedup_key,
                result="error", error=str(e),
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            print(f"[capture/{capture_source}] emit failed: {e}", file=sys.stderr)
            return 0

    def _audit(
        self,
        capture_source: str,
        target_table: str,
        op: str,
        source_uuid: str | None,
        *,
        result: str,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        """Enqueue an audit log row. Audit rows are best-effort; never raise."""
        try:
            self.buffer.enqueue(
                "dossier_capture_log",
                {
                    "capture_source": capture_source,
                    "target_table": target_table,
                    "op": op,
                    "source_uuid": source_uuid,
                    "result": result,
                    "error": error,
                    "latency_ms": latency_ms,
                },
            )
        except Exception:
            pass


class Capture:
    """Base class for all captures."""
    name: str = "base"

    def __init__(self, ctx: CaptureContext):
        self.ctx = ctx
        self._started = False

    def start(self) -> None:
        """Begin observing. Override in subclasses.

        Must be non-blocking (spawn threads internally if needed).
        Subclasses should call super().start() at the end.
        """
        self._started = True

    def stop(self) -> None:
        """Stop observing cleanly. Override in subclasses.

        Must be idempotent — start/stop/stop is legal.
        """
        self._started = False

    @property
    def running(self) -> bool:
        return self._started


__all__ = ["Capture", "CaptureContext"]

"""Mirror sync — keeps L4 mirror_* tables in step with Supabase.

Two modes:

    bootstrap()
        On first startup or after deleting buffer.db: pull the entire
        dossier_* hot tables down via REST, fill mirror_* tables.
        Updates sync_state.last_mirror_sync_epoch.

    subscribe(client)
        Open Supabase Realtime channels for each mirrored table. Every
        INSERT / UPDATE / DELETE event is applied to L4 synchronously on
        the subscriber callback thread. If the connection drops,
        Supabase-py auto-reconnects; on reconnect we call delta_resync()
        to catch up on anything missed during the gap.

The mirror tables are not the source of truth — they're a cache. The
user can `rm buffer.db && hub bootstrap` at any time to rebuild.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from .buffer import LocalBuffer
from .supabase_client import HubClient


# Which Supabase tables we mirror, and the target L4 mirror table name.
_MIRRORED_TABLES = {
    "dossier_identity":       "mirror_identity",
    "dossier_memory_items":   "mirror_memory_items",
    "dossier_projects":       "mirror_projects",
    "dossier_conversations":  "mirror_conversations",
    "dossier_messages":       "mirror_messages",
    "dossier_skills":         "mirror_skills",
    "dossier_agents":         "mirror_agents",
    "dossier_mcp_endpoints":  "mirror_mcp_endpoints",
    "dossier_hooks":          "mirror_hooks",
}


def _ts_to_epoch(value: Any) -> int | None:
    """Parse an ISO-8601 timestamp into epoch seconds, or pass through ints."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        # strip Z, parse, return epoch
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


def _to_mirror_row(supabase_table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Map a Supabase row into the shape the L4 mirror table expects."""
    out = dict(row)

    # Normalize timestamps to epoch ints (SQLite-friendly).
    for k in ("updated_at", "created_at", "captured_at"):
        if k in out:
            out[k] = _ts_to_epoch(out[k])
    if "timestamp" in out:
        out["timestamp_epoch"] = _ts_to_epoch(out.pop("timestamp"))

    # JSON columns → strings
    for k in ("frontmatter", "content_blocks", "tools", "args", "env", "headers"):
        if k in out and out[k] is not None and not isinstance(out[k], str):
            out[k] = json.dumps(out[k], ensure_ascii=False, default=str)

    # Booleans → ints
    for k in ("is_cowork", "is_shared"):
        if k in out and isinstance(out[k], bool):
            out[k] = 1 if out[k] else 0

    return out


class MirrorSync:
    def __init__(self, buffer: LocalBuffer, client: HubClient):
        self.buffer = buffer
        self.client = client
        self.stats = {
            "events_applied": 0,
            "events_skipped": 0,
            "bootstrap_rows": 0,
        }

    # ── Bootstrap: full pull ───────────────────────────────────────

    def bootstrap(self, *, page_size: int = 500) -> None:
        """First-run or reset pull. Idempotent — safe to re-run.

        Requires the client to support a basic SELECT. For SupabaseClient
        we cheat and use the postgrest endpoint via rpc-less fallback;
        for InMemoryClient we read from its `tables` dict.
        """
        from .supabase_client import SupabaseClient, InMemoryClient

        if isinstance(self.client, InMemoryClient):
            for src, dst in _MIRRORED_TABLES.items():
                for row in self.client.tables.get(src, {}).values():
                    self.buffer.mirror_upsert(dst, _to_mirror_row(src, row))
                    self.stats["bootstrap_rows"] += 1
        elif isinstance(self.client, SupabaseClient):
            for src, dst in _MIRRORED_TABLES.items():
                try:
                    rows = self.client._client.table(src).select("*").execute().data
                except Exception as e:
                    print(f"[mirror.bootstrap] {src}: {e}", file=sys.stderr)
                    continue
                for row in rows or []:
                    self.buffer.mirror_upsert(dst, _to_mirror_row(src, row))
                    self.stats["bootstrap_rows"] += 1
        else:
            print("[mirror.bootstrap] unknown client type, skipping bootstrap",
                  file=sys.stderr)

        self.buffer.set_state(
            "last_mirror_sync_epoch", str(int(time.time()))
        )

    # ── Realtime: live subscribe ──────────────────────────────────

    def subscribe(self) -> None:
        """Open realtime channels so every INSERT/UPDATE/DELETE on the
        mirrored tables lands in L4 within a few hundred ms."""
        self.client.subscribe_changes(
            tables=list(_MIRRORED_TABLES.keys()),
            on_event=self._on_realtime_event,
        )

    # ── Delta catch-up ────────────────────────────────────────────

    def delta_resync(self) -> None:
        """On reconnect, fetch everything modified since the last sync
        epoch and apply it. Supabase exposes this via the
        `dossier_delta_since` RPC defined in sql/0004_functions.sql."""
        last = int(self.buffer.get_state("last_mirror_sync_epoch", "0"))
        since_iso = datetime.fromtimestamp(last, tz=timezone.utc).isoformat()
        try:
            deltas = self.client.rpc("dossier_delta_since", {"since": since_iso})
        except Exception as e:
            print(f"[mirror.delta_resync] RPC failed: {e}", file=sys.stderr)
            return
        if not deltas:
            return
        # For each (table_name, row_id), fetch the row and upsert.
        # This is naive N+1; for a personal hub it's fine at the scale of
        # delta since a reconnect (<100 rows typical).
        from .supabase_client import SupabaseClient
        if not isinstance(self.client, SupabaseClient):
            return
        for item in deltas:
            tbl = item.get("table_name")
            rid = item.get("row_id")
            if not tbl or not rid or tbl not in _MIRRORED_TABLES:
                continue
            try:
                row = (
                    self.client._client
                    .table(tbl).select("*").eq("id", rid).single().execute().data
                )
            except Exception as e:
                print(f"[mirror.delta_resync] fetch {tbl}/{rid} failed: {e}",
                      file=sys.stderr)
                continue
            self.buffer.mirror_upsert(_MIRRORED_TABLES[tbl], _to_mirror_row(tbl, row))
        self.buffer.set_state("last_mirror_sync_epoch", str(int(time.time())))

    # ── Event handler ─────────────────────────────────────────────

    def _on_realtime_event(self, ev: dict[str, Any]) -> None:
        try:
            table = ev.get("table")
            event_type = (ev.get("eventType") or ev.get("event") or "").upper()
            mirror_table = _MIRRORED_TABLES.get(table or "")
            if not mirror_table:
                self.stats["events_skipped"] += 1
                return

            if event_type in ("INSERT", "UPDATE"):
                new = ev.get("new") or {}
                self.buffer.mirror_upsert(mirror_table, _to_mirror_row(table, new))
            elif event_type == "DELETE":
                old = ev.get("old") or {}
                row_id = old.get("id")
                if row_id:
                    self.buffer.mirror_delete(mirror_table, row_id)
            self.stats["events_applied"] += 1
            # Touch the water-mark on every event so delta_resync has the
            # right anchor after the next disconnect.
            self.buffer.set_state("last_mirror_sync_epoch", str(int(time.time())))
        except Exception as e:
            print(f"[mirror] event apply failed: {e}", file=sys.stderr)
            self.stats["events_skipped"] += 1


__all__ = ["MirrorSync"]

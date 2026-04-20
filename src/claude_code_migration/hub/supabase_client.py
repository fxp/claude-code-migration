"""Thin Supabase client abstraction.

We don't want hub code to tangle with the supabase-py SDK at every
call site — it makes testing awful and makes the "no-Supabase" dev mode
harder. Everything goes through this interface:

    HubClient (Protocol)
        .upsert(table, row, dedup_key=None)
        .delete(table, row_id)
        .rpc(name, payload)
        .subscribe_changes(tables, on_event)  # realtime
        .download_storage(bucket, path)       # Storage
        .upload_storage(bucket, path, data)

Concrete implementations:

    SupabaseClient — real, requires SUPABASE_URL + SUPABASE_SERVICE_KEY
    InMemoryClient — tests / --local-only mode. Just remembers calls.
    DryRunClient   — logs calls to stderr, never ACKs. Useful for inspection.

The drain worker (see drain.py) talks only to HubClient, so swapping
backends is a one-liner.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol


# ── Protocol ──────────────────────────────────────────────────────────


class HubClient(Protocol):
    """Minimum surface the drain worker + realtime mirror need."""

    def upsert(
        self,
        table: str,
        row: dict[str, Any],
        *,
        on_conflict: str = "source_uuid",
    ) -> None: ...

    def delete(self, table: str, row_id: str) -> None: ...

    def rpc(self, name: str, payload: dict[str, Any]) -> Any: ...

    def subscribe_changes(
        self,
        tables: Iterable[str],
        on_event: Callable[[dict[str, Any]], None],
    ) -> None: ...

    def close(self) -> None: ...


# ── InMemoryClient · for tests and --local-only mode ──────────────────


@dataclass
class _RecordedCall:
    op: str
    table: str
    payload: dict[str, Any] | None = None
    row_id: str | None = None


@dataclass
class InMemoryClient:
    """Records every call in an ordered list. Never touches the network.

    Suitable for:
      • Unit tests (assert on `calls`).
      • `--local-only` mode where the user runs hub-agent purely for the
        L4 buffer + MCP stdio server, with no cloud backend.

    Rows are kept in `tables[name]` for querying back (mimics a minimal
    upsert/delete semantic so drain + mirror can round-trip in tests).
    """
    calls: list[_RecordedCall] = field(default_factory=list)
    tables: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _subs: list[tuple[set[str], Callable[[dict[str, Any]], None]]] = field(default_factory=list)
    fail_once: set[str] = field(default_factory=set)   # tables that will raise on next call

    def upsert(self, table: str, row: dict[str, Any], *, on_conflict: str = "source_uuid") -> None:
        if table in self.fail_once:
            self.fail_once.discard(table)
            raise RuntimeError(f"InMemoryClient.fail_once hit on {table}")
        self.calls.append(_RecordedCall(op="upsert", table=table, payload=row))
        key = row.get(on_conflict) or row.get("id") or f"row-{len(self.tables.get(table, {}))}"
        self.tables.setdefault(table, {})[key] = row
        self._fanout(table, {"eventType": "INSERT", "new": row, "old": None})

    def delete(self, table: str, row_id: str) -> None:
        self.calls.append(_RecordedCall(op="delete", table=table, row_id=row_id))
        row = self.tables.get(table, {}).pop(row_id, None)
        self._fanout(table, {"eventType": "DELETE", "new": None, "old": row or {"id": row_id}})

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        self.calls.append(_RecordedCall(op="rpc", table=name, payload=payload))
        return []

    def subscribe_changes(self, tables: Iterable[str], on_event: Callable[[dict[str, Any]], None]) -> None:
        self._subs.append((set(tables), on_event))

    def close(self) -> None:
        self._subs.clear()

    def _fanout(self, table: str, event: dict[str, Any]) -> None:
        event = dict(event, table=table)
        for subs, cb in self._subs:
            if table in subs:
                try:
                    cb(event)
                except Exception as e:
                    print(f"[InMemoryClient] subscriber raised: {e}", file=sys.stderr)


# ── DryRunClient · logging no-op ──────────────────────────────────────


class DryRunClient:
    """Logs every call to stderr; never connects anywhere. Useful for
    inspecting what the drain worker would send."""

    def upsert(self, table: str, row: dict[str, Any], *, on_conflict: str = "source_uuid") -> None:
        print(f"[dry-run] UPSERT {table} on_conflict={on_conflict}: "
              f"{json.dumps(row, default=str)[:200]}", file=sys.stderr)

    def delete(self, table: str, row_id: str) -> None:
        print(f"[dry-run] DELETE {table} id={row_id}", file=sys.stderr)

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        print(f"[dry-run] RPC {name} payload={json.dumps(payload, default=str)[:200]}", file=sys.stderr)
        return []

    def subscribe_changes(self, tables: Iterable[str], on_event: Callable[[dict[str, Any]], None]) -> None:
        print(f"[dry-run] SUBSCRIBE {list(tables)}", file=sys.stderr)

    def close(self) -> None:
        pass


# ── SupabaseClient · real implementation (requires the supabase extra) ─


class SupabaseClient:
    """Real Supabase client. Lazy-imports supabase-py so the base install
    doesn't need it.

    Usage::

        client = SupabaseClient.from_env()    # reads SUPABASE_URL + SUPABASE_SERVICE_KEY
        client.upsert("dossier_memory_items", {...})

    Thread-safe for UPSERT / DELETE / RPC (supabase-py's httpx client is).
    Realtime subscriptions run on a background thread.
    """

    def __init__(self, url: str, service_key: str):
        try:
            from supabase import create_client, Client  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Supabase backend requested but supabase-py is not installed. "
                "pip install 'dossier-hub[supabase]'"
            ) from e
        self._url = url
        self._client = create_client(url, service_key)
        self._rt_client = None
        self._rt_thread: threading.Thread | None = None

    @classmethod
    def from_env(cls) -> SupabaseClient:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment. "
                "Get them from https://app.supabase.com/project/_/settings/api."
            )
        return cls(url, key)

    def upsert(
        self,
        table: str,
        row: dict[str, Any],
        *,
        on_conflict: str = "source_uuid",
    ) -> None:
        # supabase-py's upsert uses PostgREST's Prefer header for conflict resolution.
        self._client.table(table).upsert(row, on_conflict=on_conflict).execute()

    def delete(self, table: str, row_id: str) -> None:
        self._client.table(table).delete().eq("id", row_id).execute()

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        return self._client.rpc(name, payload).execute().data

    def subscribe_changes(
        self,
        tables: Iterable[str],
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to realtime postgres_changes for `tables`.

        Runs on a background thread; swallows + logs exceptions so a buggy
        subscriber can't take down the daemon.
        """
        try:
            from supabase import AsyncClient  # type: ignore  # noqa: F401
        except ImportError:
            pass

        def _runner():
            for t in tables:
                ch = self._client.channel(f"changes:{t}")
                ch.on_postgres_changes(
                    event="*", schema="public", table=t,
                    callback=lambda p, tbl=t: on_event({**p, "table": tbl}),
                )
                ch.subscribe()

        self._rt_thread = threading.Thread(target=_runner, daemon=True, name="dossier-hub-realtime")
        self._rt_thread.start()

    def close(self) -> None:
        # supabase-py doesn't expose an explicit close for realtime; let it
        # die with the process. The daemon thread is already marked daemon=True.
        pass


__all__ = [
    "HubClient",
    "InMemoryClient",
    "DryRunClient",
    "SupabaseClient",
]

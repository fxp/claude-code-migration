"""L4 · LocalBuffer — the offline-first heart of hub-agent.

Two purposes in one SQLite file (~/.dossier-hub/buffer.db by default):

  outbox
      Write queue. Every capture writes here first — synchronous, atomic,
      µs latency. An async drain worker consumes FIFO and UPSERTs into
      Supabase. On success → delete the row; on failure → bump attempts,
      retry with exponential backoff, eventually move to dead_letter.

  mirror
      Read cache. A Supabase Realtime subscriber keeps the hot dossier_*
      tables mirrored locally. MCP tools read from here exclusively:
      offline-ok, <1ms latency, zero network round-trips.

Invariants:
  • Writes never block on the network. The outbox ACKs in SQLite time.
  • Reads never cross the network. The mirror is the only read path.
  • Duplicate writes are idempotent via `dedup_key` → Supabase UNIQUE(source_uuid).
  • Cleanup is explicit: drain worker deletes successful rows; a nightly
    vacuum compacts the DB.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

# ── Schema ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- pragma optimizations: WAL for concurrent reads, NORMAL sync for speed
pragma journal_mode = WAL;
pragma synchronous = NORMAL;
pragma temp_store = MEMORY;
pragma mmap_size = 268435456;    -- 256 MB mmap

-- ─── Outbox: pending writes to Supabase ─────────────────────────────
create table if not exists outbox (
  id          integer primary key autoincrement,
  target      text not null,       -- 'dossier_memory_items' | 'dossier_messages' | ...
  op          text not null,       -- 'upsert' | 'insert' | 'delete'
  payload     text not null,       -- JSON serialized Dossier fragment
  dedup_key   text,                -- typically source_uuid; drives Supabase ON CONFLICT
  attempts    integer default 0,
  next_retry  integer default (unixepoch()),   -- epoch seconds
  last_error  text,
  created_at  integer default (unixepoch())
);
create index if not exists outbox_next_retry
  on outbox (next_retry)
  where attempts < 10;

-- ─── Dead letter: gave up after too many retries ────────────────────
create table if not exists dead_letter (
  id              integer primary key autoincrement,
  original_row    text not null,
  first_error_at  integer,
  gave_up_at      integer default (unixepoch())
);

-- ─── Mirror of hot Supabase tables ──────────────────────────────────
-- We only mirror what MCP tools read from. Vault is NEVER mirrored
-- (secrets stay server-side and decrypt on demand). raw_archives aren't
-- mirrored either — they're too big and cold.

create table if not exists mirror_identity (
  id              text primary key,
  source_platform text,
  account_uuid    text,
  email           text,
  display_name    text,
  org_uuid        text,
  org_name        text,
  org_role        text,
  billing_type    text,
  is_cowork       integer,
  updated_at      integer
);

create table if not exists mirror_memory_items (
  id              text primary key,
  source_uuid     text,
  kind            text,
  name            text,
  content         text,
  frontmatter     text,
  source_platform text,
  updated_at      integer
);
create index if not exists mirror_memory_kind
  on mirror_memory_items (kind);
-- Self-contained FTS5 — easier to keep in sync than external-content tables.
-- Maintained via an AFTER INSERT trigger below.
create virtual table if not exists mirror_memory_fts
  using fts5(id UNINDEXED, name, content,
             tokenize='unicode61 remove_diacritics 2');

create table if not exists mirror_projects (
  id              text primary key,
  slug            text unique,
  name            text,
  description     text,
  context         text,
  prompt_template text,
  is_shared       integer,
  updated_at      integer
);

create table if not exists mirror_conversations (
  id              text primary key,
  source_uuid     text,
  platform        text,
  title           text,
  model           text,
  project_id      text,
  created_at      integer,
  updated_at      integer
);

create table if not exists mirror_messages (
  id              text primary key,
  source_uuid     text,
  conversation_id text,
  role            text,
  content_text    text,
  content_blocks  text,
  thinking        text,
  timestamp_epoch integer,
  captured_at     integer
);
create index if not exists mirror_messages_conv
  on mirror_messages (conversation_id, timestamp_epoch desc);
create virtual table if not exists mirror_messages_fts
  using fts5(id UNINDEXED, content_text,
             tokenize='unicode61 remove_diacritics 2');

create table if not exists mirror_skills (
  id              text primary key,
  name            text unique,
  description     text,
  body            text,
  frontmatter     text,
  source_platform text,
  source_plugin   text,
  updated_at      integer
);

create table if not exists mirror_agents (
  id              text primary key,
  name            text unique,
  description     text,
  model           text,
  instructions    text,
  tools           text,
  updated_at      integer
);

create table if not exists mirror_mcp_endpoints (
  id              text primary key,
  name            text,
  scope           text,
  transport       text,
  url             text,
  command         text,
  args            text,
  env             text,
  headers         text,
  plugin_owner    text
);

create table if not exists mirror_hooks (
  id              text primary key,
  event           text,
  matcher         text,
  type            text,
  command         text,
  timeout_seconds integer,
  scope           text
);

-- ─── Sync state: watermark for delta_since on reconnect ─────────────
create table if not exists sync_state (
  key             text primary key,
  value           text
);

-- Initialize water-mark if first run
insert or ignore into sync_state (key, value) values
  ('last_mirror_sync_epoch', '0'),
  ('schema_version', '1');
"""

# Mirror table → columns metadata (for upsert helpers). Keep in sync
# with _SCHEMA_SQL above.
_MIRROR_COLS: dict[str, tuple[str, ...]] = {
    "mirror_identity": (
        "id", "source_platform", "account_uuid", "email", "display_name",
        "org_uuid", "org_name", "org_role", "billing_type", "is_cowork", "updated_at",
    ),
    "mirror_memory_items": (
        "id", "source_uuid", "kind", "name", "content", "frontmatter",
        "source_platform", "updated_at",
    ),
    "mirror_projects": (
        "id", "slug", "name", "description", "context", "prompt_template",
        "is_shared", "updated_at",
    ),
    "mirror_conversations": (
        "id", "source_uuid", "platform", "title", "model", "project_id",
        "created_at", "updated_at",
    ),
    "mirror_messages": (
        "id", "source_uuid", "conversation_id", "role", "content_text",
        "content_blocks", "thinking", "timestamp_epoch", "captured_at",
    ),
    "mirror_skills": (
        "id", "name", "description", "body", "frontmatter", "source_platform",
        "source_plugin", "updated_at",
    ),
    "mirror_agents": (
        "id", "name", "description", "model", "instructions", "tools", "updated_at",
    ),
    "mirror_mcp_endpoints": (
        "id", "name", "scope", "transport", "url", "command", "args", "env",
        "headers", "plugin_owner",
    ),
    "mirror_hooks": (
        "id", "event", "matcher", "type", "command", "timeout_seconds", "scope",
    ),
}


# ── Types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutboxEntry:
    """One pending write, post-dequeue."""
    id: int
    target: str
    op: str
    payload: dict[str, Any]
    dedup_key: str | None
    attempts: int
    last_error: str | None


# ── LocalBuffer ───────────────────────────────────────────────────────


class LocalBuffer:
    """L4 buffer. Thread-safe via SQLite's own locking (WAL mode)."""

    def __init__(self, db_path: Path | str = "~/.dossier-hub/buffer.db"):
        self.path = Path(db_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path,
            isolation_level=None,        # autocommit; we do explicit BEGIN
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    # ── Outbox: write side ─────────────────────────────────────────

    def enqueue(
        self,
        target: str,
        payload: dict[str, Any],
        *,
        op: str = "upsert",
        dedup_key: str | None = None,
    ) -> int:
        """Append one pending write. Returns outbox row id.

        Callers should go through `Redactor.scrub(payload)` before this.
        """
        cur = self._conn.execute(
            "insert into outbox (target, op, payload, dedup_key) values (?, ?, ?, ?)",
            (target, op, json.dumps(payload, ensure_ascii=False, default=str), dedup_key),
        )
        return int(cur.lastrowid or 0)

    def outbox_size(self) -> int:
        row = self._conn.execute("select count(*) as n from outbox").fetchone()
        return int(row["n"])

    def peek_due(self, limit: int = 100) -> list[OutboxEntry]:
        """Return up to `limit` rows whose `next_retry <= now`.

        Drain worker calls this, tries each one, then calls
        `mark_done` / `mark_failed` / `give_up`.
        """
        rows = self._conn.execute(
            """
            select id, target, op, payload, dedup_key, attempts, last_error
              from outbox
             where next_retry <= unixepoch()
             order by id asc
             limit ?
            """,
            (limit,),
        ).fetchall()
        return [
            OutboxEntry(
                id=r["id"],
                target=r["target"],
                op=r["op"],
                payload=json.loads(r["payload"]),
                dedup_key=r["dedup_key"],
                attempts=r["attempts"],
                last_error=r["last_error"],
            )
            for r in rows
        ]

    def mark_done(self, entry_id: int) -> None:
        self._conn.execute("delete from outbox where id = ?", (entry_id,))

    def mark_failed(self, entry_id: int, error: str) -> None:
        """Record failure; schedule retry with exponential backoff (max 1h)."""
        row = self._conn.execute(
            "select attempts from outbox where id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        attempts = row["attempts"] + 1
        # Backoff: 1s, 2s, 4s, ..., capped at 3600s
        delay = min(2 ** attempts, 3600)
        self._conn.execute(
            """
            update outbox
               set attempts = ?, last_error = ?, next_retry = unixepoch() + ?
             where id = ?
            """,
            (attempts, error, delay, entry_id),
        )

    def give_up(self, entry_id: int, reason: str = "attempts exhausted") -> None:
        """Move a row to dead_letter after too many failures."""
        row = self._conn.execute(
            "select * from outbox where id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        serialized = json.dumps({k: row[k] for k in row.keys()}, default=str)
        first_error_at = int(row["created_at"])
        with self._tx():
            self._conn.execute(
                "insert into dead_letter (original_row, first_error_at) values (?, ?)",
                (serialized, first_error_at),
            )
            self._conn.execute("delete from outbox where id = ?", (entry_id,))
        # reason is captured as part of last_error already; noted here for clarity
        _ = reason

    def dead_letter_count(self) -> int:
        row = self._conn.execute("select count(*) as n from dead_letter").fetchone()
        return int(row["n"])

    # ── Mirror: read side ──────────────────────────────────────────

    def mirror_upsert(self, table: str, row: dict[str, Any]) -> None:
        """Insert-or-replace a row in a mirror table.

        Silently tolerates extra columns (ignored) and missing ones
        (left as NULL). Call this from the Realtime subscriber.
        """
        cols = _MIRROR_COLS.get(table)
        if not cols:
            raise ValueError(f"unknown mirror table: {table}")

        values = tuple(row.get(c) for c in cols)
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        with self._tx():
            self._conn.execute(
                f"insert or replace into {table} ({col_list}) values ({placeholders})",
                values,
            )
            # Keep FTS in sync manually (self-contained FTS5 tables).
            if table == "mirror_memory_items":
                self._conn.execute("delete from mirror_memory_fts where id = ?", (row.get("id"),))
                self._conn.execute(
                    "insert into mirror_memory_fts (id, name, content) values (?, ?, ?)",
                    (row.get("id"), row.get("name") or "", row.get("content") or ""),
                )
            elif table == "mirror_messages":
                self._conn.execute("delete from mirror_messages_fts where id = ?", (row.get("id"),))
                self._conn.execute(
                    "insert into mirror_messages_fts (id, content_text) values (?, ?)",
                    (row.get("id"), row.get("content_text") or ""),
                )

    def mirror_delete(self, table: str, row_id: str) -> None:
        cols = _MIRROR_COLS.get(table)
        if not cols:
            raise ValueError(f"unknown mirror table: {table}")
        with self._tx():
            self._conn.execute(f"delete from {table} where id = ?", (row_id,))
            if table == "mirror_memory_items":
                self._conn.execute("delete from mirror_memory_fts where id = ?", (row_id,))
            elif table == "mirror_messages":
                self._conn.execute("delete from mirror_messages_fts where id = ?", (row_id,))

    def mirror_search_memory(
        self, query: str, *, kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """FTS5 search over mirror_memory_items (joined via id)."""
        # Self-contained FTS: mirror_memory_fts has (id, name, content),
        # column index 2 is 'content' for snippet().
        if kind:
            rows = self._conn.execute(
                """
                select m.*,
                       snippet(mirror_memory_fts, 2, '<b>', '</b>', '...', 16) as snippet
                  from mirror_memory_fts f
                  join mirror_memory_items m on m.id = f.id
                 where mirror_memory_fts match ?
                   and m.kind = ?
                 order by rank
                 limit ?
                """,
                (query, kind, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                select m.*,
                       snippet(mirror_memory_fts, 2, '<b>', '</b>', '...', 16) as snippet
                  from mirror_memory_fts f
                  join mirror_memory_items m on m.id = f.id
                 where mirror_memory_fts match ?
                 order by rank
                 limit ?
                """,
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mirror_list_skills(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "select id, name, description, source_plugin from mirror_skills order by name"
        ).fetchall()
        return [dict(r) for r in rows]

    def mirror_read_skill(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "select * from mirror_skills where name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def mirror_read_profile(
        self, category: str | None = None
    ) -> list[dict[str, Any]]:
        if category:
            rows = self._conn.execute(
                "select * from mirror_memory_items where kind = 'user_profile' and (name = ? or frontmatter like ?)",
                (category, f'%"{category}"%'),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "select * from mirror_memory_items where kind = 'user_profile' order by updated_at desc"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Sync state ─────────────────────────────────────────────────

    def get_state(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "select value from sync_state where key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "insert or replace into sync_state (key, value) values (?, ?)",
            (key, value),
        )

    # ── Housekeeping ───────────────────────────────────────────────

    def vacuum(self) -> None:
        """Run nightly: compact DB + rebuild FTS indexes if needed."""
        self._conn.execute("vacuum")
        self._conn.execute("insert into mirror_memory_fts(mirror_memory_fts) values('optimize')")
        self._conn.execute("insert into mirror_messages_fts(mirror_messages_fts) values('optimize')")

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    def __enter__(self) -> LocalBuffer:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[None]:
        self._conn.execute("begin")
        try:
            yield
            self._conn.execute("commit")
        except Exception:
            self._conn.execute("rollback")
            raise


__all__ = ["LocalBuffer", "OutboxEntry"]

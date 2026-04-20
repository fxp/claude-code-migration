# dossier-hub · Architecture

## Why this exists

[`claude-code-migration`](https://github.com/fxp/claude-code-migration) is
a one-shot tool: scan your Claude Code state → a Workspace Dossier →
apply to another agent. Great for migration, useless for "keep my
agents in sync every day."

`dossier-hub` is the always-on counterpart. Captures stream into a
Supabase-backed data store in real time; MCP tools let any agent read
the data back with <1ms latency.

## Layer model

```
┌──────────────────────────────────────────────────────────────────┐
│  L1 + L2 · Supabase (source of truth)                            │
│    • 20 Postgres tables mirroring the Dossier schema              │
│    • pgvector + tsvector + GIN (semantic + keyword search)        │
│    • Realtime change subscriptions                                │
│    • Edge Functions (embeddings, web captures)                    │
│    • Row-Level Security per user + vault trust levels             │
│    • Storage buckets for large blobs                              │
└──────────────▲───────────────────────────────────┬────────────────┘
               │ drain worker                       │ realtime
               │ (outbox → Supabase, retry)         │ subscription
               │                                    │
┌──────────────┴────────────────────────────────────┴───────────────┐
│  L4 · Local Buffer (SQLite, ~/.dossier-hub/buffer.db)             │
│    • outbox         — pending writes (offline-safe, idempotent)   │
│    • mirror_*       — hot table cache (MCP reads < 1ms, offline)  │
│    • mirror_*_fts   — FTS5 indexes                                │
│    • dead_letter    — gave-up writes for manual inspection        │
│    • sync_state     — file offsets, water-marks                   │
└──────────────▲────────────────────────────────▲──────────────────┘
               │ captures push                   │ MCP tools read
               │ (non-blocking)                  │ (local-only)
               │                                 │
┌──────────────┴─────────────────────────────────┴─────────────────┐
│  L3 · hub-agent daemon                                           │
│    Captures (pluggable):                                         │
│      • claude_code_fs    — tail ~/.claude/projects/*/*.jsonl     │
│      • claude_desktop    — MCP proxy + IndexedDB poller          │
│      • cursor            — fsnotify state.db                      │
│      • codex_cli         — fsnotify session db                    │
│      • browser_web       — receiver for browser-extension POSTs   │
│    All captures flow through the Redactor middleware              │
│    (ccm.redactor) before landing in the outbox.                   │
│                                                                  │
│    MCP server (stdio+HTTP):                                      │
│      Exposes mirror_* tables as MCP tools any agent can call.     │
└───────────────────────────────────────────────────────────────────┘
```

## The four layers in detail

### L1 · Source of truth: Supabase Postgres

20 tables, all prefixed `dossier_*`, mirroring the ccm `WorkspaceDossier`
dataclass fields:

| Table | Dossier field |
|---|---|
| `dossier_identity` | `Identity` |
| `dossier_memory_items` | `MemoryItem` (unified across `memory.*`) |
| `dossier_projects` / `dossier_project_docs` / `dossier_project_events` | `Project` |
| `dossier_conversations` / `dossier_messages` / `dossier_attachments` / `dossier_artifacts` | `Conversation` |
| `dossier_skills` / `dossier_skill_files` | `Skill` |
| `dossier_agents` | `Agent` |
| `dossier_mcp_endpoints` / `dossier_plugins` / `dossier_marketplaces` | `McpEndpoint` / `Plugin` / `Marketplace` |
| `dossier_hooks` / `dossier_scheduled_tasks` | `Hook` / `ScheduledTask` |
| `dossier_vault_entries` | (new: age-encrypted secrets) |
| `dossier_inbox_messages` | (new: agent-to-agent async) |
| `dossier_raw_archives` | (new: Claude-Code-specific extras) |
| `dossier_capture_log` | (internal: audit) |

Every row carries `source_uuid` (dedup key) + `source_platform` +
`owner_uid` (Supabase `auth.uid()`) + `captured_at` + optionally
`updated_at`.

### L2 · Indexes (inside L1)

- `tsvector` columns + GIN indexes for keyword FTS (`search_memory`,
  `search_messages`).
- `pgvector(1536)` columns for semantic search via embeddings.
- Trigram indexes for fuzzy name lookups (`projects.slug`,
  `skills.name`).
- Time-series btrees for conversation timelines.

### L3 · hub-agent daemon

The moving part. Responsibilities:

- **Captures** (one per source platform):
  - `ClaudeCodeFSCapture` — `watchdog` fsnotify over
    `~/.claude/projects/<enc>/*.jsonl`. Byte-offset-tracked incremental
    tail. Daemon restart resumes from last offset.
  - (future) `ClaudeDesktopCapture` — MCP proxy wrapper + IndexedDB poll
  - (future) `CursorCapture` / `CodexCapture` — fsnotify on local state
  - (future) `BrowserCapture` — HTTP receiver for browser-extension POSTs
- **Redactor middleware** (`dossier_hub.redact.Redactor`) — wraps
  `claude_code_migration.redactor.redact`, accumulates findings for
  later vault upload.
- **DrainWorker** — background thread flushing outbox → Supabase with
  exponential backoff and dead-letter.
- **MirrorSync** — Supabase Realtime subscriber keeping the L4 mirror
  tables in sync.
- **MCP server** (future: `src/dossier_hub/mcp/`) — stdio + HTTP
  JSON-RPC 2.0 endpoint exposing tools backed by mirror reads.

Config is a `HubConfig` dataclass; CLI flags map onto it.

### L4 · Local buffer (SQLite)

The offline-first heart. Two roles in one SQLite file:

**Write side · outbox**
- Captures insert rows, ACK in µs.
- Drain worker polls FIFO, tries Supabase UPSERT via
  `on_conflict=<dedup column>`.
- Failures → `attempts++`, `next_retry = now + 2^attempts` (capped 1h).
- Attempts ≥ 10 → move to `dead_letter` table.

**Read side · mirror**
- One `mirror_<table>` per hot Supabase table (memory / projects /
  conversations / messages / skills / agents / mcp_endpoints / hooks).
- Vault and raw_archives are NOT mirrored (too sensitive / too big).
- `MirrorSync.bootstrap()` does initial pull.
- `MirrorSync.subscribe()` keeps up in real time.
- `MirrorSync.delta_resync()` catches up after reconnect using the
  `dossier_delta_since(ts)` Supabase RPC.
- Self-contained FTS5 on `mirror_memory_items.content` and
  `mirror_messages.content_text` for the MCP `search_memory` tool.

All MCP `read_*` tool calls query L4 exclusively — **no MCP read ever
crosses the network**. Writes land in L4 first and get reconciled to L1
asynchronously.

### Backup (not a "layer", but worth naming)

For catastrophic recovery (Supabase account locked, data loss, etc.):

- **Daily**: `pg_dump $SUPABASE_DB_URL | gzip > backup-$(date +%F).sql.gz`
- **Weekly**: `hub-agent panic-dump` (planned) — full L4 tarball to cold
  storage.
- **On-demand**: `ccm panic-backup` from the ccm repo grabs the
  local `~/.claude/` state as a neuDrive-canonical tar.gz.

## Data-flow examples

### Capture · Claude Code appends a message

```
User prompts Claude Code
  → Claude Code writes line to ~/.claude/projects/<enc>/<session>.jsonl
    → watchdog fires on_modified
      → ClaudeCodeFSCapture._tail_file(path)
        → seek to last offset (from sync_state)
        → readline() loop; parse each complete JSONL line
          → CaptureContext.emit(target='dossier_messages', row=...)
            → Redactor.scrub(row)           [sk-ant-*, ghp_*, Bearer... → ${CC_*}]
            → LocalBuffer.enqueue(row)      [SQLite INSERT, ~50µs]
            → audit-log enqueue
```

No network, no blocking. Offset persisted so a daemon crash mid-tail
resumes cleanly.

### Drain · Worker pushes to Supabase

```
DrainWorker (background thread, 1s cadence while empty):
  peek_due(limit=50)
    → for each entry:
      client.upsert(entry.target, entry.payload, on_conflict=<per-table key>)
      if ok:
        buffer.mark_done(entry.id)         [DELETE from outbox]
      else:
        buffer.mark_failed(entry.id, error)
        [next_retry = now + backoff]
```

Supabase UPSERT idempotency (unique constraint on source_uuid / slug /
name / plugin_id / scope depending on table) handles re-emissions.

### Read · Agent calls `search_memory`

```
Claude Code (MCP client)
  → hub-agent MCP server (stdio JSON-RPC 2.0):
     method=tools/call name=search_memory args={"query": "concise"}
     → LocalBuffer.mirror_search_memory("concise")
       → SELECT ... FROM mirror_memory_fts f JOIN mirror_memory_items m
         WHERE mirror_memory_fts MATCH 'concise' ORDER BY rank LIMIT 20;
     → return [{kind, name, snippet, score}, ...]
  Response round-trip: <1 ms total, zero network.
```

## Failure modes

| Failure | What happens |
|---|---|
| Supabase down | Captures keep writing to outbox. Reads hit mirror (stale but usable). Drain backs off. |
| Network down | Same as above. |
| Daemon crash | Captures stop. On restart, offset-tracked tailers resume from last byte. |
| L4 corrupted | `rm buffer.db && hub init && hub bootstrap` — pulls fresh from Supabase. |
| Supabase account lost | Restore from `pg_dump` backup or `ccm panic-backup` archive. |
| Secret leaks into capture | Redactor scrubs before outbox insert. Findings accumulated for vault upload. |
| ZIP / file bombs | ccm's scanner already rejects; hub inherits via `claude-code-migration>=1.0.0`. |

## What's still missing (tracked in issues)

- MCP server implementation (tools/list + tools/call handlers)
- Claude Desktop captures (MCP proxy + IndexedDB poller)
- Cursor / Codex / Windsurf captures
- Browser extension for Claude Web / ChatGPT
- Age-encrypted vault write path + MCP `read_secret` / `write_secret`
- Edge Function for async embedding generation
- `hub panic-dump` CLI verb wrapping `ccm panic-backup`
- Two-way sync (push from L4 mirror back when offline-authored rows land
  after a reconnect — currently outbox-only is sufficient for capture
  workloads)

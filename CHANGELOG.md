# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Dates are YYYY-MM-DD.

## [Unreleased]

### Added

- **2026 surface area coverage beyond CLAUDE.md** — picks up the rest of
  `~/.claude/` and per-plugin data documented at
  <https://code.claude.com/docs/en/claude-directory>:
  - **Slash commands** — `~/.claude/commands/**/*.md` and
    `<proj>/.claude/commands/**/*.md`. Subdirectories namespace commands
    (e.g. `frontend/test.md` → `/frontend:test`). Frontmatter fields
    `description` / `allowed-tools` / `argument-hint` are surfaced
    explicitly for adapter convenience.
  - **Themes** — `~/.claude/themes/*` captured verbatim (file format
    intentionally unspecified by the spec).
  - **Keybindings** — `~/.claude/keybindings.json` preserved as-is.
  - **Plugin `bin/` executables** (Week 14, v2.1.91) — files inside a
    plugin's `bin/` directory are PATH-injected by Claude Code while
    the plugin is enabled. Captured per-plugin so target users know
    what binaries the agent expected on PATH.
  - **Plugin-bundled commands** — `<plugin>/commands/**/*.md` (prefixed
    with plugin name).
  - **Plugin-bundled agents** — `<plugin>/agents/*.md` (prefixed with
    plugin name).
- **Adapter `_archive/claude-extras/`** — every captured bucket lands as
  a readable file with reconstructed frontmatter so a user can hand-port
  individual commands/themes/bindings into a target agent that has an
  equivalent. `INDEX.md` lists each artifact and its origin.
- 7 new tests in `tests/test_claude_extras.py` covering namespacing, prefix
  injection, plugin `bin/`, end-to-end scan into a fake CLAUDE_CONFIG_DIR,
  and adapter archive output. Suite: 136 → 143 passing.

- **2026 CLAUDE.md discovery spec coverage** — scanner now matches the load
  semantics documented at <https://code.claude.com/docs/en/memory>:
  - `./.claude/CLAUDE.md` — alternate project-level location (was silently
    missed when a user put instructions there instead of `./CLAUDE.md`)
  - **Ancestor walk** — CLAUDE.md + CLAUDE.local.md from every parent
    directory up to fs root (Claude Code concatenates all of them)
  - **Subdirectory enumeration** — CLAUDE.md files inside the project that
    Claude Code lazy-loads; previously invisible to the migration
  - **`@path` import expansion** — recursive up to 5 hops per spec, with
    fenced-code-block detection so `@foo` inside a triple-backtick example
    is not mistakenly imported
  - **Enterprise managed-policy CLAUDE.md** — OS-specific path
    (`/Library/Application Support/ClaudeCode/CLAUDE.md` on macOS,
    `/etc/claude-code/CLAUDE.md` on Linux/WSL,
    `C:\Program Files\ClaudeCode\CLAUDE.md` on Windows)
- **Adapter `_archive/claude-md-tree/`** — every discovered CLAUDE.md bucket
  lands in a readable per-file dump with an `INDEX.md` explaining origin.
  We don't flatten — concatenation order depends on runtime cwd, so the
  user re-composes manually if the target agent has different discovery
  rules.
- `tests/test_claude_md_discovery.py` — 8 tests covering every new branch
  (including depth-limit and fenced-code guard). Suite: 128 → 136 passing.

### Verified

- Against a complex real-world project, 3 subdirectory CLAUDE.md files that
  were previously silently dropped now land in the dossier and
  `_archive/claude-md-tree/` of every target.

## [1.2.0] — 2026-04-19

Turns the L4 mirror into a queryable MCP surface so any agent (Claude Code,
Cursor, Codex CLI, OpenCode, Windsurf) can read the user's Workspace Dossier
without a network hop. This is the step that makes hub mode "通" — before
1.2.0, data flowed in; now it flows back out.

### Added

- **`ccm hub mcp-serve`** — stdio MCP server (JSON-RPC 2.0) that exposes the
  local SQLite mirror as a tool surface. Read-only by design: writes must go
  through captures so they pass through the redactor. Startup requires no
  network — works on an airplane, works before Supabase is configured.
  - `--list` flag dumps tool schemas as JSON (no server spawn), useful for
    wiring the server into a client `mcp_config.json`.
  - `--allow-empty` lets you start the server against a fresh buffer (handy
    for demos; normally you'd run `ccm hub serve` first to populate it).
- **New subpackage `claude_code_migration.hub.mcp`** — the MCP implementation:
  - `server.py` — single-threaded JSON-RPC 2.0 dispatcher over line-delimited
    stdio. Implements `initialize`, `ping`, `tools/list`, `tools/call`,
    `shutdown`, and silently ignores `notifications/*`. Errors map to the
    standard JSON-RPC codes (`-32700` parse / `-32600` invalid request /
    `-32601` method-not-found / `-32602` invalid params / `-32603` internal).
  - `tools.py` — 14 tools out of the box:
    - `search_memory` — FTS5 over memory items, optional `kind` filter
    - `read_profile` — user-profile memory items (ex. `~/.claude/CLAUDE.md`)
    - `list_memory`, `read_memory` — enumerate / dereference memory items
    - `list_skills`, `read_skill` — skills index + full body+frontmatter
    - `list_agents` — subagents with model + description
    - `list_hooks` — event / matcher / command / scope
    - `list_mcp_endpoints` — configured MCP servers (stdio + SSE)
    - `list_projects`, `get_project` — project cards + prompt templates
    - `search_conversations` — FTS5 over messages with highlight snippets
    - `get_conversation` — one conversation's ordered message stream
    - `get_stats` — buffer row counts + outbox size + water-mark
  - `ToolRegistry` / `Tool` types — pluggable; additional captures can
    register new tools at daemon boot.
- **41 new tests** (`tests/test_hub_mcp.py`) covering tool dispatch,
  JSON-RPC parsing, every error code, `initialize` / `tools/list` /
  `tools/call` end-to-end over StringIO, and the CLI `--list` path via
  subprocess. Suite is now **180 passing**.
- `McpServer` and `build_default_registry` re-exported from
  `claude_code_migration.hub` for Python-API users.

### Fixed

- **`claude_code_fs.py`: watchdog is now a soft import.** Previously the
  capture module raised `RuntimeError("watchdog is required")` at import
  time, which cascaded through `captures/__init__.py` → `daemon.py` →
  `hub/__main__.py` and disabled the entire `ccm hub` subcommand group
  when the `[hub]` extra wasn't installed. Now the import silently
  degrades; `ClaudeCodeFSCapture.start()` is the only place that fails,
  and it fails with an actionable message. `ccm hub init` /
  `ccm hub mcp-serve` / `ccm hub status` now work with just the base
  install.

### Design notes

The MCP surface is deliberately **read-only**. Every tool reads from L4
mirror tables — never the network, never the source-of-truth Supabase
rows. This has three properties:

1. **Offline-first**: the MCP server keeps answering queries on an
   airplane or in a CI sandbox; only the data freshness depends on the
   mirror's last sync.
2. **Zero secret leak**: the `dossier_vault_entries` table is *never*
   mirrored to L4 by design, so no MCP tool can expose it even by
   accident. Secret reads will go through a separate authenticated path
   when/if we ship a vault tool.
3. **Redaction is enforced at ingest**: writes flow capture → redactor →
   outbox → Supabase → realtime → mirror. By the time the MCP surface
   sees a row, it has already been scrubbed.

## [1.1.0] — 2026-04-20

Folds the standalone [`dossier-hub`](https://github.com/fxp/dossier-hub)
project into this one as an optional subpackage (`claude_code_migration.hub`)
and subcommand group (`ccm hub ...`). One install, one CLI, one docs site.

**Non-breaking**: the `ccm` 3-step CLI (export / apply / migrate / scan /
push-hub / panic-backup) is unchanged. The hub feature is gated behind an
optional dep group — `pip install 'claude-code-migration[hub]'`.

### Added

- **`ccm hub` subcommand group** — always-on hub mode integrated as a
  sibling of the existing migration verbs. Verbs:
  - `ccm hub init` — create `~/.dossier-hub/buffer.db` + sample config
  - `ccm hub serve` — run the daemon (`--local-only` / `--remote` / `--dry-run`)
  - `ccm hub status` — outbox size, dead-letter count, mirror water-mark
  - `ccm hub migrate` — apply `sql/*.sql` schema to your Supabase project
  - `ccm hub bootstrap` — first-run Supabase → L4 mirror pull
  - `ccm hub drain-once` — one-shot outbox flush
- **New subpackage `claude_code_migration.hub`** — ~1 200 LOC moved in:
  - `buffer.py` — L4 SQLite outbox + mirror + self-contained FTS5
  - `drain.py` — async worker with exponential backoff + dead-letter
  - `mirror.py` — Supabase Realtime subscriber + delta_resync
  - `redact.py` — capture middleware wrapping the existing ccm redactor,
    accumulates findings for later vault upload
  - `daemon.py` — `HubDaemon` / `HubConfig` orchestrating captures + workers
  - `supabase_client.py` — `HubClient` protocol + InMemory (tests) /
    DryRun (stderr) / real SupabaseClient implementations
  - `captures/base.py` + `captures/claude_code_fs.py` — plugin base class
    + real-time tailer for `~/.claude/projects/<enc>/*.jsonl` with
    byte-offset-tracked incremental reads, daemon-restart resume, and
    partial-line tolerance.
- **20 dossier tables + RLS + pgvector/tsvector + RPC functions** bundled
  as SQL migrations under `hub/sql/` (shipped as package-data so
  `ccm hub migrate` finds them in wheel installs).
- **37 new tests** covering the hub subsystem (buffer, drain, capture,
  redactor middleware, offline-integration). Total: 92 → **129** passing.
- **`docs/HUB_ARCHITECTURE.md`** explaining the layered design (L1 + L2
  Supabase / L3 hub-agent / L4 SQLite buffer).

### Changed

- Existing `claude_code_migration.hub` module → renamed to
  `claude_code_migration.neudrive` to make room for the new subpackage.
  Public import surface unchanged (both `NeuDriveHub` and
  `push_scan_to_hub` keep working), but internal imports and
  `tests/test_hub.py` → `tests/test_neudrive_client.py`.
- `pyproject.toml`: new `[hub]` extra depending on `watchdog>=4.0`,
  `supabase>=2.5`, `psycopg[binary]>=3.1`. Core `ccm` keeps only its
  minimal `httpx>=0.27` dep, so users who only ever migrate can skip the
  larger deps.
- Keyword list expanded (`claude-desktop`, `supabase`, `hub`, `always-on`).

### Fixed

Two bugs caught during the merge + live smoke:

- SQLite FTS5 external-content tables (`content=..., content_rowid=...`)
  weren't syncing via `insert or replace` — rewrote as self-contained
  FTS5 tables with explicit INSERT/DELETE inside `mirror_upsert` /
  `mirror_delete`, wrapped in a single transaction.
- `f.tell()` inside a `for line in f` loop is forbidden in text mode
  — rewrote the JSONL tailer as a `readline()` loop for byte-accurate
  resume.

## [1.0.0] — 2026-04-20

Stable 1.0 release. Same code as v0.2.1 — the version that has been
running on the GitHub Pages landing page and validated against real
local data — promoted to 1.0 as a stability commitment for the
`ccm export` / `ccm apply` / `ccm migrate` CLI surface and the
`WorkspaceDossier` schema.

### Stability commitment

- The 3-step CLI (`ccm export`, `ccm apply`, `ccm migrate`, plus
  `ccm scan` and `ccm push-hub`) and their flags are stable. Removals
  or breaking changes will require a 2.x major version.
- `WorkspaceDossier` (schema `IR_VERSION = "1.0"`) is stable. New fields
  may be added; existing fields will not be renamed or removed without
  a major version bump.
- Public Python API exported from the top-level package (`scan_claude_code`,
  `save_scan`, `parse_cowork_zip`, `WorkspaceDossier`, `CanonicalData`,
  `scan_secrets`, `redact`) is stable.

### Notes

- No source-code changes vs v0.2.1; only version-string bumps in
  `pyproject.toml`, `src/claude_code_migration/__init__.py`, and the
  landing-page badges.

## [0.3.0] — 2026-04-19

Adds `ccm panic-backup` — a one-command emergency capture designed for the
moment you suspect your Claude account is about to get throttled or banned.
Unlike `ccm export` (which is vendor-neutral, redacted, and aimed at
migration), `panic-backup` deliberately includes OAuth tokens, plugin state,
and raw MCP Bearer keys so the archive is a true defensive snapshot.

> **Versioning note**: 0.3.0 and 1.0.0 were cut in parallel from v0.2.1
> (0.3.0 added panic-backup, 1.0.0 rebranded the migration surface as
> stable). Both are subsumed into 1.1.0.

### Added

- **`ccm panic-backup`** CLI verb (also `claude_code_migration.panic_backup`
  Python API). Output is a single `.tar.gz` at chmod 0o600 following
  neuDrive's canonical path convention (`/identity`, `/memory`, `/projects`,
  `/skills`, `/conversations`, `/roles`) plus two extra trees:
  - `/credentials/` — Tier-2 secrets: `~/.claude.json` `oauthAccount` block
    (access + refresh tokens), `mcpServers` with plaintext Bearer tokens,
    `~/.claude/plugins/` full tree, `mcp-needs-auth-cache.json`,
    `settings*.json`. Includes a `README.md` with danger language.
  - `/claude-code-extras/` — Tier-3 raw files: shell snapshots, session-env,
    file-history, history.jsonl, raw-scan.json.
- **RESTORE.md** auto-generated inside every archive with step-by-step
  recovery for three scenarios: new Claude Code account, migrate to a
  different agent, upload to neuDrive Hub.
- **`manifest.json`** inside every archive describes what was captured
  (tier counts, canonical path mapping, warnings).
- **`--cowork-zip` flag** on panic-backup: if the user has already
  triggered the official Settings → Privacy → Export data ZIP, unpacks
  each conversation into `/conversations/claude-chat/<uuid>/` canonical
  paths so the archive covers Tier-1 cloud data too.
- **`--redact-credentials` flag**: skip `/credentials/` for a "safe to
  share" archive.
- 10 new tests in `tests/test_panic_backup.py`.

### Changed

- Archive layout mirrors upstream neuDrive (`agi-bar/neuDrive`'s
  `hubpath/canonical_paths.go`) so `neu sync import panic-backup.tar.gz`
  works directly — panic-backup doubles as a Hub import bundle.
- Scanner's `max_session_body_mb` default raised from 32 MB to 256 MB
  when called from `panic_backup()`.
- Top-level package exports extended with `panic_backup` +
  `PanicBackupResult`.

## [0.2.1] — 2026-04-19

Patch release. Fixes a crash on the chat / cowork apply path discovered
while exercising `ccm export` / `ccm apply` against real local data right
after the v0.2.0 cut.

### Fixed

- **HIGH**: `ccm apply` crashed with `TypeError: asdict() should be
  called on dataclass instances` whenever the loaded dossier contained a
  message with at least one attachment. `_rehydrate_dossier` rebuilt
  `Conversation.messages` and `Conversation.artifacts` but missed
  `Message.attachments`, leaving them as raw dicts; `to_cowork_export`
  then fed those dicts to `asdict()`. Affected every chat-migration and
  cowork-migration apply, since attachments are the common case.
- 4 new regression tests in `tests/test_dossier_rehydrate.py` pin down
  rehydration of every nested dataclass list (`Message.attachments`,
  `Conversation.artifacts`, `Project.docs`) plus a full chat-shaped
  round-trip → cowork export.

### Site

- Landing page badge corrected (`v1.0` → `v0.2.0`, then `v0.2.1` here).
- Added Open Graph / Twitter card meta and inline SVG favicon so shared
  links render a preview and the browser tab is not blank.

## [0.2.0] — 2026-04-19

First beta release. The codebase has been validated against 50 real local
Claude Code projects (23 with sessions) × 4 target agents = 92 migrations
without failure, and carries 88 tests covering every source parser,
target adapter, security guard, and hub integration path.

### Added

- **Workspace Dossier (项目档案)** — the vendor-neutral intermediate the
  whole tool revolves around. Same class that was internally called
  `CanonicalData` / "Canonical IR"; the new name is what users actually
  see. Both names are exported from the top-level package.
- **3-step CLI** — `ccm export` (source → dossier.json), `ccm apply`
  (dossier + target → generated project dir), `ccm migrate` (one-shot).
  `ccm scan` kept as a legacy / power-user verb that dumps the raw
  scanner dict (used by `ccm push-hub`).
- **Scanner coverage → 60+ data types**. Previous releases captured only
  metadata for sessions / plans / todos / history. Now also captures:
  - `projects/<enc>/*.jsonl` full message bodies
  - `projects/<enc>/<uuid>/subagents/` sub-agent transcripts
  - `projects/<enc>/<uuid>/tool-results/` cached tool payloads
  - `plans/*.md`, `todos/*.json`, `history.jsonl` full contents
  - `~/.claude.json` per-project state + top-level meta
  - `shell-snapshots/`, `session-env/`, `file-history/`,
    `mcp-needs-auth-cache.json`
  - `--max-session-mb` flag with cap warning when hit (default 32 MB)
- **Reverse source parsers** — cursor / opencode / hermes / windsurf →
  dossier, enabling any-to-any migration (e.g. Cursor → OpenCode,
  Hermes → Windsurf) without going through Claude.
- **`redactor.py`** module — scrubs plaintext secrets at every disk-write
  boundary. Combines key-name whitelist (ported from neuDrive upstream's
  `looksSensitiveKey`) with free-form regex scanning (sk-ant-*, ghp_*,
  AKIA*, Bearer, PEM private keys, BigModel 32.16). Redacted values
  become `${CC_<PATH>}` env-var placeholders; the adapters pass these
  through to target configs unchanged.
- **10 hub integration tests** via a local HTTP mock that mimics
  neuDrive's `{ok:true,data:{}}` envelope — verifies every NeuDriveHub
  method's verb / path / payload and end-to-end `push_scan_to_hub`
  routing (profile, scratch memory, skills, cowork conversations).
- `py.typed` marker so downstream type checkers pick up the package's
  inline type annotations.
- `CHANGELOG.md` (this file) and `RELEASE.md` runbook.

### Changed

- **File permissions for dossier / scan artifacts are now 0o600**
  (user-only, was world-readable 0o644). Sibling
  `<stem>.secrets-manifest.json` lists SHA256 prefixes of everything
  scrubbed so users have an audit trail.
- `--token` on `ccm push-hub` is discouraged (still works, prints a
  stderr warning that it's visible in `ps aux`). New `--token-stdin`
  reads the token from stdin; `NEUDRIVE_TOKEN` env var remains the
  preferred path.
- `--in-place` on `ccm apply` / `ccm migrate` runs a `git status --porcelain`
  check before writing. Dirty trees are rejected with an enumerated diff;
  pass `--force` to override.
- Python classifier bumped from `3 - Alpha` to `4 - Beta`. Python 3.13
  and 3.14 added to the supported versions matrix.
- 5 silent `except Exception: pass` blocks in scanner / sources /
  adapters were replaced with narrowed exceptions + stderr warnings
  (previously swallowed parse errors).

### Fixed

- **CRITICAL**: MCP `Authorization: Bearer <token>` no longer appears
  verbatim in `dossier.json`. Previously the redactor did not exist, so
  the intermediate IR file shipped plaintext secrets the README told
  users to commit.
- **CRITICAL**: Pasted API keys in `history.jsonl` `display` fields and
  session message bodies are redacted. Free-form regex catches secrets
  inside prose, which neuDrive's `key=value` line-oriented redactor
  misses.
- **CRITICAL**: Shell snapshots, session-env bundles, and file-history
  entries are scanned recursively — they commonly contain `export
  FOO_API_KEY=...` lines that prior versions captured but never scrubbed.
- **HIGH**: `parse_cowork_zip` now rejects ZIP entries over 500 MB
  uncompressed or with compression ratio > 1000× (zip-bomb guard,
  `ZipBombError`), and rejects entry names containing `..` or starting
  with `/`.
- **MEDIUM**: `NeuDriveHub.write_file` rejects paths with `..` / `.`
  segments or NUL bytes client-side. Previously only the server
  validated.
- **MEDIUM**: ZIP member matching uses exact basename instead of
  `endswith()`, which previously would have matched crafted entries like
  `evilconversations.json` or `../conversations.json`.
- **MEDIUM**: `push_scan_to_hub` no longer silently drops HTTP errors.
  Per-item failures are accumulated and printed to stderr at the end;
  the return dict carries an `errors` count.
- Hermes adapter KeyError on any account that has Cowork plugins
  installed — `to_adapter_scan()` now re-exposes the legacy
  `plugin_name` field alongside IR-renamed `name`.
- 4 unused imports removed (`Attachment`, `MemoryItem`, `Any`,
  `build_universal_agents_md`, `os` — each in different modules).
- `__version__` drift between `src/__init__.py` (was `0.1.0`) and
  `pyproject.toml` (`0.2.0`) — now synced.

### Removed

- Untracked working-note drafts (`ideas-full.md`,
  `reference/agent-frameworks-comparison.md`) — were one-off scratchpads
  never referenced from tracked files.

## [0.1.0] — 2026-04

Initial proof of concept. Single source (Claude Code) → four targets
(Hermes, OpenCode, Cursor, Windsurf). Cowork ZIP parser. Metadata-only
session capture. Legacy `ccm scan` + `ccm migrate` CLI. neuDrive Hub
HTTP client.

[Unreleased]: https://github.com/fxp/claude-code-migration/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/fxp/claude-code-migration/releases/tag/v1.1.0
[1.0.0]: https://github.com/fxp/claude-code-migration/releases/tag/v1.0.0
[0.3.0]: https://github.com/fxp/claude-code-migration/releases/tag/v0.3.0
[0.2.1]: https://github.com/fxp/claude-code-migration/releases/tag/v0.2.1
[0.2.0]: https://github.com/fxp/claude-code-migration/releases/tag/v0.2.0
[0.1.0]: https://github.com/fxp/claude-code-migration/releases/tag/v0.1.0

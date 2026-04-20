# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Dates are YYYY-MM-DD.

## [Unreleased]

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
[0.2.1]: https://github.com/fxp/claude-code-migration/releases/tag/v0.2.1
[0.2.0]: https://github.com/fxp/claude-code-migration/releases/tag/v0.2.0
[0.1.0]: https://github.com/fxp/claude-code-migration/releases/tag/v0.1.0

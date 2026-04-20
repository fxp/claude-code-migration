-- ═══════════════════════════════════════════════════════════════════════════
-- dossier-hub · Workspace Dossier schema
--
-- 20 tables mirroring the ccm `Dossier` type system (CanonicalData in code).
-- Apply in order: 0001 (this file) → 0002 (indexes) → 0003 (RLS) → 0004 (RPC).
--
-- Every table carries:
--   • id              uuid primary key (server-generated)
--   • source_uuid     text UNIQUE nullable — the original platform's uuid if any,
--                     used as the dedup / upsert target from L4 outbox
--   • captured_at     timestamptz default now() — when hub-agent observed it
--   • source_platform text — 'claude-code' | 'claude-desktop' | 'claude-chat' |
--                     'cursor' | 'opencode' | 'hermes' | 'windsurf' | 'codex' | ...
-- ═══════════════════════════════════════════════════════════════════════════

create extension if not exists "uuid-ossp";
create extension if not exists "vector";      -- pgvector for embeddings
create extension if not exists "pg_trgm";     -- fuzzy text search fallback

-- ───────────────────────────────────────────────────────────────────────────
-- Identity (singleton-ish: one row per account_uuid you've observed)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_identity (
  id                uuid primary key default uuid_generate_v4(),
  source_platform   text not null,
  account_uuid      text,
  email             text,
  display_name      text,
  org_uuid          text,
  org_name          text,
  org_role          text,                 -- admin | owner | member
  workspace_role    text,
  billing_type      text,                 -- apple_subscription | team_plan | enterprise_plan
  is_cowork         boolean default false,
  captured_at       timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (source_platform, account_uuid)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Memory · everything "remember this" — user prefs, project context, rules, etc.
-- Dossier.memory.* all collapse here with `kind` discriminator.
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_memory_items (
  id                uuid primary key default uuid_generate_v4(),
  source_uuid       text unique,
  kind              text not null,         -- 'user_profile' | 'project' | 'feedback' |
                                           -- 'scratch' | 'rules' | 'output_style' | 'agent_memory'
  name              text,
  content           text not null,
  frontmatter       jsonb,
  origin_session_id text,
  source_platform   text,
  embedding         vector(1536),           -- filled async by an edge function
  captured_at       timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Projects · Claude Projects / Cowork workspaces / local code projects with CLAUDE.md
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_projects (
  id                uuid primary key default uuid_generate_v4(),
  source_uuid       text,                   -- nullable; local projects have no uuid
  slug              text not null unique,
  name              text not null,
  description       text,
  context           text,                   -- CLAUDE.md body
  prompt_template   text,                   -- Claude Projects custom instructions
  is_shared         boolean default false,
  workspace_id      text,                   -- Cowork
  source_platform   text,
  captured_at       timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create table dossier_project_docs (
  id                uuid primary key default uuid_generate_v4(),
  project_id        uuid not null references dossier_projects on delete cascade,
  filename          text not null,
  content           text,
  mime_type         text default 'text/markdown',
  captured_at       timestamptz not null default now()
);

-- Event log (new — not in ccm Dossier, but essential for realtime agent activity)
-- This is where PostToolUse hooks, fsnotify events, MCP proxy taps land.
create table dossier_project_events (
  id                bigserial primary key,
  project_id        uuid references dossier_projects on delete cascade,
  event_type        text not null,          -- 'tool_use' | 'tool_result' | 'edit' |
                                           -- 'commit' | 'test_run' | 'todo_update' | ...
  actor             text,                   -- which agent triggered it
  source_platform   text,
  payload           jsonb,
  created_at        timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Conversations · chat threads from any source
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_conversations (
  id                uuid primary key default uuid_generate_v4(),
  source_uuid       text unique,
  platform          text not null,          -- 'claude-code' | 'claude-chat' | 'claude-desktop' | ...
  title             text,
  model             text,
  project_id        uuid references dossier_projects on delete set null,
  created_at        timestamptz,
  updated_at        timestamptz not null default now(),
  captured_at       timestamptz not null default now()
);

create table dossier_messages (
  id                uuid primary key default uuid_generate_v4(),
  source_uuid       text unique,
  conversation_id   uuid not null references dossier_conversations on delete cascade,
  role              text not null,          -- user | assistant | system | tool
  content_text      text,                   -- flattened text for FTS
  content_blocks    jsonb,                  -- original content[] blocks (text/thinking/tool_use/tool_result)
  thinking          text,
  "timestamp"       timestamptz,
  embedding         vector(1536),
  captured_at       timestamptz not null default now()
);

create table dossier_attachments (
  id                uuid primary key default uuid_generate_v4(),
  message_id        uuid not null references dossier_messages on delete cascade,
  filename          text not null,
  content           text,                   -- inlined if small
  storage_path      text,                   -- ref to Supabase Storage bucket if large
  mime_type         text,
  url               text,                   -- signed URL if external
  captured_at       timestamptz not null default now()
);

create table dossier_artifacts (
  id                uuid primary key default uuid_generate_v4(),
  source_uuid       text,
  conversation_id   uuid references dossier_conversations on delete cascade,
  title             text,
  mime_type         text,
  extension         text,
  final_content     text,
  version_count     integer default 1,
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Skills · reusable capabilities (SKILL.md + extras)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_skills (
  id                uuid primary key default uuid_generate_v4(),
  name              text not null unique,
  description       text,
  body              text,                   -- SKILL.md body sans frontmatter
  frontmatter       jsonb,
  allowed_tools     jsonb,                  -- list<string>
  source_platform   text,
  source_plugin     text,                   -- "figma" / "cowork-plugin" / ""
  captured_at       timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create table dossier_skill_files (
  id                uuid primary key default uuid_generate_v4(),
  skill_id          uuid not null references dossier_skills on delete cascade,
  rel_path          text not null,
  content           text,
  storage_path      text,                   -- for binary extras
  unique (skill_id, rel_path)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Agents (aka Claude Code sub-agents, "roles" in neuDrive terminology)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_agents (
  id                uuid primary key default uuid_generate_v4(),
  name              text not null unique,
  description       text,
  model             text,
  color             text,
  instructions      text,
  tools             jsonb,                  -- list<string>
  mode              text default 'subagent',-- 'subagent' | 'primary' | 'all'
  source_platform   text,
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- MCP · endpoints, plugins, marketplaces
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_mcp_endpoints (
  id                uuid primary key default uuid_generate_v4(),
  name              text not null,
  transport         text not null,          -- 'http' | 'sse' | 'stdio'
  url               text,
  command           text,
  args              jsonb,                  -- list<string>
  env               jsonb,                  -- key-value; values redacted to ${CC_*}
  headers           jsonb,                  -- key-value; values redacted
  scope             text not null default 'global',  -- 'global' | 'project' | 'plugin'
  plugin_owner      text,
  has_embedded_secret boolean default false,
  captured_at       timestamptz not null default now(),
  unique (scope, name, plugin_owner)
);

create table dossier_plugins (
  id                uuid primary key default uuid_generate_v4(),
  plugin_id         text not null unique,   -- "figma@claude-plugins-official"
  plugin_name       text not null,
  marketplace       text not null,
  version           text,
  install_path      text,
  scope             text,
  installed_at      timestamptz,
  git_commit_sha    text,
  manifest          jsonb,
  bundled_mcp       jsonb,                  -- list<string>: mcp names (refs to endpoints)
  bundled_skills    jsonb,                  -- list<string>: skill names
  captured_at       timestamptz not null default now()
);

create table dossier_marketplaces (
  id                uuid primary key default uuid_generate_v4(),
  name              text not null unique,
  source_type       text,                   -- 'github' | 'url' | 'git-subdir' | 'npm' | 'path'
  source_spec       jsonb,
  install_location  text,
  manifest          jsonb,
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Hooks · automation (settings.json hooks)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_hooks (
  id                uuid primary key default uuid_generate_v4(),
  event             text not null,          -- 'PostToolUse' | 'SessionStart' | 'PreToolUse' | ...
  matcher           text,
  type              text default 'command', -- 'command' | 'http' | 'prompt' | 'agent'
  command           text,
  timeout_seconds   integer default 30,
  scope             text default 'global',  -- 'global' | 'project' | 'project_local'
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Scheduled tasks
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_scheduled_tasks (
  id                uuid primary key default uuid_generate_v4(),
  name              text not null unique,
  schedule          text default 'manual',  -- cron expression or 'manual'
  prompt            text,
  frontmatter       jsonb,
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Vault · encrypted secrets (age / libsodium sealed box)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_vault_entries (
  id                uuid primary key default uuid_generate_v4(),
  scope             text not null unique,   -- 'claude/web-search-prime/bearer'
  ciphertext        bytea not null,         -- age-encrypted (client-side before upload)
  kind              text,                   -- 'bearer_token' | 'oauth_refresh' | 'api_key' | 'pem'
  suggested_env_var text,                   -- CC_MCP_WEB_SEARCH_PRIME_TOKEN
  trust_level       smallint default 3,     -- 0 (public) .. 4 (maximum; only primary agent)
  metadata          jsonb,                  -- sha256_prefix, source path, etc.
  captured_at       timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Inbox · agent-to-agent async messaging (new; not in ccm Dossier)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_inbox_messages (
  id                uuid primary key default uuid_generate_v4(),
  from_role         text,
  to_role           text not null,
  status            text not null default 'new',   -- 'new' | 'read' | 'processed' | 'archived'
  subject           text,
  payload           jsonb,
  created_at        timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Raw archives · everything Claude-Code-specific that doesn't fit canonical
-- (shell snapshots, session-env, file-history, history.jsonl, subagents, tool-results)
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_raw_archives (
  id                uuid primary key default uuid_generate_v4(),
  kind              text not null,          -- 'shell_snapshot' | 'session_env' | 'file_history' |
                                            -- 'history_entry' | 'subagent_trace' | 'tool_result'
  source_path       text,
  source_session_id text,
  content           text,
  storage_path      text,                   -- for >64KB blobs
  metadata          jsonb,
  captured_at       timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Capture audit · every tool call / edit observed, for debugging + auditability
-- ───────────────────────────────────────────────────────────────────────────
create table dossier_capture_log (
  id                bigserial primary key,
  capture_source    text not null,          -- 'claude_code_fs' | 'claude_desktop_mcp_proxy' | ...
  target_table      text,
  op                text,                   -- 'insert' | 'upsert' | 'delete'
  source_uuid       text,
  result            text,                   -- 'ok' | 'redacted' | 'skipped' | 'error'
  error             text,
  latency_ms        integer,
  created_at        timestamptz not null default now()
);

-- Tracking of mutation for quick delta queries
-- (used by L4 mirror on reconnect: "give me everything since my last sync")
create or replace function dossier_touch_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger touch_identity      before update on dossier_identity      for each row execute function dossier_touch_updated_at();
create trigger touch_memory_items  before update on dossier_memory_items  for each row execute function dossier_touch_updated_at();
create trigger touch_projects      before update on dossier_projects      for each row execute function dossier_touch_updated_at();
create trigger touch_conversations before update on dossier_conversations for each row execute function dossier_touch_updated_at();
create trigger touch_skills        before update on dossier_skills        for each row execute function dossier_touch_updated_at();
create trigger touch_vault         before update on dossier_vault_entries for each row execute function dossier_touch_updated_at();

comment on schema public is 'dossier-hub · Workspace Dossier v1';

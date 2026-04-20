-- ═══════════════════════════════════════════════════════════════════════════
-- dossier-hub · Indexes
--
-- Three kinds:
--   • tsvector + GIN  — keyword full-text search (search_memory / search_messages)
--   • pgvector ivfflat — semantic search via embeddings (lists=100 for <1M rows)
--   • btree            — foreign keys + time-series lookups
-- ═══════════════════════════════════════════════════════════════════════════

-- ── FTS: memory_items ─────────────────────────────────────────────────────
alter table dossier_memory_items
  add column if not exists content_tsv tsvector generated always as (
    setweight(to_tsvector('simple', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(content, '')), 'B')
  ) stored;
create index if not exists dossier_memory_items_tsv_gin
  on dossier_memory_items using gin (content_tsv);
create index if not exists dossier_memory_items_kind
  on dossier_memory_items (kind, captured_at desc);
create index if not exists dossier_memory_items_platform
  on dossier_memory_items (source_platform, captured_at desc);

-- ── FTS: messages ─────────────────────────────────────────────────────────
alter table dossier_messages
  add column if not exists content_tsv tsvector generated always as (
    to_tsvector('simple', coalesce(content_text, ''))
  ) stored;
create index if not exists dossier_messages_tsv_gin
  on dossier_messages using gin (content_tsv);
create index if not exists dossier_messages_conv_ts
  on dossier_messages (conversation_id, "timestamp" desc);

-- ── Semantic search: pgvector ivfflat cosine ──────────────────────────────
-- 1536-dim (OpenAI text-embedding-3-small / BigModel embedding-3). ivfflat is
-- good enough for a personal hub (< 1M rows). For multi-user SaaS scale,
-- switch to HNSW in a follow-up migration.
create index if not exists dossier_memory_items_embed_ivfflat
  on dossier_memory_items using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists dossier_messages_embed_ivfflat
  on dossier_messages using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ── Conversations + projects timeline indexes ─────────────────────────────
create index if not exists dossier_conversations_platform_ts
  on dossier_conversations (platform, updated_at desc);
create index if not exists dossier_conversations_project
  on dossier_conversations (project_id, updated_at desc)
  where project_id is not null;

create index if not exists dossier_project_events_project_ts
  on dossier_project_events (project_id, created_at desc);
create index if not exists dossier_project_events_type
  on dossier_project_events (event_type, created_at desc);

-- ── Attachments / artifacts ───────────────────────────────────────────────
create index if not exists dossier_attachments_msg
  on dossier_attachments (message_id);
create index if not exists dossier_artifacts_conv
  on dossier_artifacts (conversation_id);

-- ── MCP + skills lookups ──────────────────────────────────────────────────
create index if not exists dossier_mcp_endpoints_scope
  on dossier_mcp_endpoints (scope, name);
create index if not exists dossier_skills_plugin
  on dossier_skills (source_plugin)
  where source_plugin is not null;

-- ── Hooks + scheduled tasks ───────────────────────────────────────────────
create index if not exists dossier_hooks_event
  on dossier_hooks (event);

-- ── Vault + inbox ─────────────────────────────────────────────────────────
create index if not exists dossier_vault_trust
  on dossier_vault_entries (trust_level, scope);
create index if not exists dossier_inbox_to_status
  on dossier_inbox_messages (to_role, status, created_at desc);

-- ── Raw archives ──────────────────────────────────────────────────────────
create index if not exists dossier_raw_archives_kind_session
  on dossier_raw_archives (kind, source_session_id, captured_at desc);

-- ── Capture audit log ─────────────────────────────────────────────────────
create index if not exists dossier_capture_log_source_ts
  on dossier_capture_log (capture_source, created_at desc);
create index if not exists dossier_capture_log_errors
  on dossier_capture_log (result, created_at desc)
  where result = 'error';

-- ── Trigram fallback for fuzzy search on names ────────────────────────────
create index if not exists dossier_projects_slug_trgm
  on dossier_projects using gin (slug gin_trgm_ops);
create index if not exists dossier_skills_name_trgm
  on dossier_skills using gin (name gin_trgm_ops);

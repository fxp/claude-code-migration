-- ═══════════════════════════════════════════════════════════════════════════
-- dossier-hub · Row-Level Security
--
-- Replaces neuDrive's trust-level gating. Every dossier row gets scoped by:
--   1. owner_uid — auth.uid() from Supabase JWT (default RLS plumbing)
--   2. trust_level — on vault_entries only, integer 0-4
--   3. scope_prefix — on vault_entries only, the JWT's allowed_scope_prefix claim
--
-- For the personal / single-user case, every dossier_* row belongs to the
-- user whose JWT spawned the capture. Multi-user would require adding
-- owner_uid to each table; for this MVP we use RLS to lock everything to the
-- authenticated user via service role during writes, and to their scoped
-- JWT during reads.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Enable RLS on all dossier_* tables ───────────────────────────────────
alter table dossier_identity          enable row level security;
alter table dossier_memory_items      enable row level security;
alter table dossier_projects          enable row level security;
alter table dossier_project_docs      enable row level security;
alter table dossier_project_events    enable row level security;
alter table dossier_conversations     enable row level security;
alter table dossier_messages          enable row level security;
alter table dossier_attachments       enable row level security;
alter table dossier_artifacts         enable row level security;
alter table dossier_skills            enable row level security;
alter table dossier_skill_files       enable row level security;
alter table dossier_agents            enable row level security;
alter table dossier_mcp_endpoints     enable row level security;
alter table dossier_plugins           enable row level security;
alter table dossier_marketplaces      enable row level security;
alter table dossier_hooks             enable row level security;
alter table dossier_scheduled_tasks   enable row level security;
alter table dossier_vault_entries     enable row level security;
alter table dossier_inbox_messages    enable row level security;
alter table dossier_raw_archives      enable row level security;
alter table dossier_capture_log       enable row level security;

-- ── Owner column on every table (nullable for now; gets populated by
--    captures via a trigger that reads auth.uid() at insert time). ────────
-- For single-user personal hubs, everything is owned by a single uid;
-- for future team use this is the sharding key.
do $$
declare
  tbl text;
begin
  foreach tbl in array ARRAY[
    'dossier_identity','dossier_memory_items','dossier_projects',
    'dossier_project_docs','dossier_project_events','dossier_conversations',
    'dossier_messages','dossier_attachments','dossier_artifacts',
    'dossier_skills','dossier_skill_files','dossier_agents',
    'dossier_mcp_endpoints','dossier_plugins','dossier_marketplaces',
    'dossier_hooks','dossier_scheduled_tasks','dossier_vault_entries',
    'dossier_inbox_messages','dossier_raw_archives','dossier_capture_log'
  ] loop
    execute format('alter table %I add column if not exists owner_uid uuid default auth.uid()', tbl);
    execute format('create index if not exists %I on %I (owner_uid)', tbl || '_owner_idx', tbl);
  end loop;
end$$;

-- ── Read / write policies for authenticated user ─────────────────────────
-- Service role bypasses RLS automatically; normal authenticated users see
-- only their own rows.
do $$
declare
  tbl text;
begin
  foreach tbl in array ARRAY[
    'dossier_identity','dossier_memory_items','dossier_projects',
    'dossier_project_docs','dossier_project_events','dossier_conversations',
    'dossier_messages','dossier_attachments','dossier_artifacts',
    'dossier_skills','dossier_skill_files','dossier_agents',
    'dossier_mcp_endpoints','dossier_plugins','dossier_marketplaces',
    'dossier_hooks','dossier_scheduled_tasks','dossier_inbox_messages',
    'dossier_raw_archives','dossier_capture_log'
  ] loop
    execute format('drop policy if exists owner_read on %I', tbl);
    execute format('create policy owner_read on %I for select using (owner_uid = auth.uid())', tbl);
    execute format('drop policy if exists owner_write on %I', tbl);
    execute format('create policy owner_write on %I for all using (owner_uid = auth.uid()) with check (owner_uid = auth.uid())', tbl);
  end loop;
end$$;

-- ── Vault special-case: trust-level + scope-prefix gate ──────────────────
-- JWT claims expected (set by the auth layer when issuing per-agent tokens):
--   max_trust_level: int 0..4
--   allowed_scope_prefix: text (e.g. "claude/")
drop policy if exists vault_owner_plus_trust on dossier_vault_entries;
create policy vault_owner_plus_trust on dossier_vault_entries
  for select using (
    owner_uid = auth.uid()
    and trust_level <= coalesce((auth.jwt() ->> 'max_trust_level')::int, 4)
    and scope like coalesce((auth.jwt() ->> 'allowed_scope_prefix') || '%', '%')
  );
-- Writes always go through service role (hub-agent holds the key).
drop policy if exists vault_owner_write on dossier_vault_entries;
create policy vault_owner_write on dossier_vault_entries
  for insert with check (owner_uid = auth.uid());
create policy vault_owner_update on dossier_vault_entries
  for update using (owner_uid = auth.uid()) with check (owner_uid = auth.uid());
create policy vault_owner_delete on dossier_vault_entries
  for delete using (owner_uid = auth.uid());

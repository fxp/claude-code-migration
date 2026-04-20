-- ═══════════════════════════════════════════════════════════════════════════
-- dossier-hub · RPC functions
--
-- These are the "high-level verbs" that MCP tools map onto. Agents don't
-- write raw SQL — they call Supabase RPC which enforces the right search
-- ranking + security.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── semantic_search ───────────────────────────────────────────────────────
-- Combines pgvector cosine distance with tsvector keyword match. Returns the
-- union sorted by a hybrid score. Scopes: 'memory' | 'messages' | 'all'.
create or replace function dossier_semantic_search(
  q text,
  q_embedding vector(1536) default null,
  scope text default 'all',
  k integer default 20
) returns table (
  kind text,
  id uuid,
  title text,
  snippet text,
  score float,
  captured_at timestamptz
) language plpgsql stable as $$
begin
  return query
  with ranked_memory as (
    select
      'memory'::text as kind,
      m.id,
      coalesce(m.name, m.kind) as title,
      substring(m.content, 1, 240) as snippet,
      (
        case when q_embedding is not null
          then 1 - (m.embedding <=> q_embedding)
          else 0
        end
        + ts_rank_cd(m.content_tsv, websearch_to_tsquery('simple', q)) * 0.5
      ) as score,
      m.captured_at
    from dossier_memory_items m
    where (scope in ('memory','all'))
      and (
        (q_embedding is not null and m.embedding is not null)
        or m.content_tsv @@ websearch_to_tsquery('simple', q)
      )
  ),
  ranked_messages as (
    select
      'message'::text as kind,
      msg.id,
      coalesce(c.title, 'conversation ' || substring(c.id::text, 1, 8)) as title,
      substring(msg.content_text, 1, 240) as snippet,
      (
        case when q_embedding is not null
          then 1 - (msg.embedding <=> q_embedding)
          else 0
        end
        + ts_rank_cd(msg.content_tsv, websearch_to_tsquery('simple', q)) * 0.5
      ) as score,
      msg.captured_at
    from dossier_messages msg
    join dossier_conversations c on c.id = msg.conversation_id
    where (scope in ('messages','all'))
      and (
        (q_embedding is not null and msg.embedding is not null)
        or msg.content_tsv @@ websearch_to_tsquery('simple', q)
      )
  )
  select * from ranked_memory
  union all
  select * from ranked_messages
  order by score desc nulls last
  limit k;
end;
$$;

-- ── save_memory ───────────────────────────────────────────────────────────
-- The `save_memory` MCP tool calls this. Idempotent on (owner_uid, name,
-- content) to avoid duplicates when a capture + manual both land the same bit.
create or replace function dossier_save_memory(
  items jsonb                             -- [{kind, name, content, frontmatter, source_platform}]
) returns setof dossier_memory_items language plpgsql as $$
declare
  item jsonb;
  inserted dossier_memory_items;
begin
  for item in select * from jsonb_array_elements(items) loop
    insert into dossier_memory_items (
      kind, name, content, frontmatter, source_platform, origin_session_id
    ) values (
      coalesce(item->>'kind', 'scratch'),
      item->>'name',
      item->>'content',
      (item->'frontmatter'),
      item->>'source_platform',
      item->>'origin_session_id'
    )
    returning * into inserted;
    return next inserted;
  end loop;
end;
$$;

-- ── log_action ────────────────────────────────────────────────────────────
-- Append a project event. Used by hook captures, MCP proxy taps, etc.
create or replace function dossier_log_action(
  project_slug text,
  event_type text,
  actor text,
  payload jsonb,
  src_platform text default null
) returns dossier_project_events language plpgsql as $$
declare
  proj_id uuid;
  result dossier_project_events;
begin
  select id into proj_id from dossier_projects where slug = project_slug;
  insert into dossier_project_events (project_id, event_type, actor, payload, source_platform)
    values (proj_id, event_type, actor, payload, src_platform)
    returning * into result;
  return result;
end;
$$;

-- ── delta_since ───────────────────────────────────────────────────────────
-- Used by the L4 mirror on reconnect: "give me everything new since my
-- last sync water-mark". Returns a lightweight summary per table so the
-- mirror can pull only deltas.
create or replace function dossier_delta_since(since timestamptz)
returns table (
  table_name text,
  row_id uuid,
  updated_at timestamptz
) language sql stable as $$
  select 'dossier_memory_items'::text, m.id, m.updated_at
    from dossier_memory_items m where m.updated_at > since
  union all
  select 'dossier_projects', p.id, p.updated_at
    from dossier_projects p where p.updated_at > since
  union all
  select 'dossier_conversations', c.id, c.updated_at
    from dossier_conversations c where c.updated_at > since
  union all
  select 'dossier_skills', s.id, s.updated_at
    from dossier_skills s where s.updated_at > since
  union all
  select 'dossier_messages', msg.id, msg.captured_at
    from dossier_messages msg where msg.captured_at > since
  order by 3 asc;
$$;

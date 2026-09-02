-- Private knowledge base for the Philippine Data Privacy AI Assistant.
-- React never connects to these tables directly. FastAPI uses the server-only
-- Supabase service-role key as the sole application data-access layer.

create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create table public.documents (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text,
  source_type text not null check (source_type in (
    'RA_10173', 'DPA_IRR', 'NPC_CIRCULAR', 'NPC_ADVISORY',
    'NPC_ADVISORY_OPINION', 'NPC_PUBLIC_ADVISORY', 'OTHER_NPC_ISSUANCE'
  )),
  source_url text,
  storage_path text,
  publication_date date,
  document_date date,
  version text,
  content_hash text,
  is_authoritative boolean not null default true,
  is_active boolean not null default true,
  processing_status text not null default 'pending'
    check (processing_status in ('pending', 'processing', 'ready', 'failed')),
  processing_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint documents_official_source check (
    source_url is null or source_url ~ '^https://([a-z0-9-]+\.)*privacy\.gov\.ph(/|$)'
  ),
  constraint documents_storage_path_unique unique (storage_path),
  constraint documents_content_hash_unique unique (content_hash)
);

create table public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null check (length(btrim(content)) > 0),
  section text,
  page_number integer check (page_number is null or page_number > 0),
  source_url text,
  metadata jsonb not null default '{}'::jsonb,
  embedding extensions.vector(768) not null,
  search_vector tsvector generated always as (
    setweight(to_tsvector('english'::regconfig, coalesce(section, '')), 'A') ||
    setweight(to_tsvector('english'::regconfig, content), 'B')
  ) stored,
  created_at timestamptz not null default now(),
  constraint document_chunks_document_index_unique unique (document_id, chunk_index)
);

create index document_chunks_document_id_idx on public.document_chunks (document_id);
create index document_chunks_search_vector_idx on public.document_chunks using gin (search_vector);
create index document_chunks_embedding_hnsw_idx
  on public.document_chunks using hnsw (embedding extensions.vector_cosine_ops);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger documents_set_updated_at
before update on public.documents
for each row execute function public.set_updated_at();

create or replace function public.match_document_chunks(
  query_embedding extensions.vector(768),
  match_threshold double precision default 0.70,
  match_count integer default 8,
  filter_source_types text[] default null
)
returns table (
  chunk_id uuid, document_id uuid, content text, similarity double precision,
  source_title text, source_type text, source_url text, page_number integer,
  section text, publication_date date
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    dc.id, d.id, dc.content,
    1 - (dc.embedding operator(extensions.<=>) query_embedding),
    d.title, d.source_type, coalesce(dc.source_url, d.source_url),
    dc.page_number, dc.section, d.publication_date
  from public.document_chunks dc
  join public.documents d on d.id = dc.document_id
  where d.is_active = true
    and d.processing_status = 'ready'
    and (filter_source_types is null or d.source_type = any(filter_source_types))
    and 1 - (dc.embedding operator(extensions.<=>) query_embedding)
      >= greatest(-1, least(match_threshold, 1))
  order by
    dc.embedding operator(extensions.<=>) query_embedding,
    case d.source_type
      when 'RA_10173' then 1 when 'DPA_IRR' then 2 when 'NPC_CIRCULAR' then 3
      when 'NPC_ADVISORY' then 4 when 'NPC_ADVISORY_OPINION' then 5
      when 'NPC_PUBLIC_ADVISORY' then 6 else 7
    end,
    d.publication_date desc nulls last
  limit least(greatest(match_count, 1), 50);
$$;

create or replace function public.search_document_chunks(
  query_text text,
  match_count integer default 8,
  filter_source_types text[] default null
)
returns table (
  chunk_id uuid, document_id uuid, content text, rank real,
  source_title text, source_type text, source_url text, page_number integer,
  section text, publication_date date
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    dc.id, d.id, dc.content,
    ts_rank_cd(dc.search_vector, websearch_to_tsquery('english'::regconfig, query_text)),
    d.title, d.source_type, coalesce(dc.source_url, d.source_url),
    dc.page_number, dc.section, d.publication_date
  from public.document_chunks dc
  join public.documents d on d.id = dc.document_id
  where length(btrim(query_text)) > 0
    and d.is_active = true
    and d.processing_status = 'ready'
    and (filter_source_types is null or d.source_type = any(filter_source_types))
    and dc.search_vector @@ websearch_to_tsquery('english'::regconfig, query_text)
  order by
    ts_rank_cd(dc.search_vector, websearch_to_tsquery('english'::regconfig, query_text)) desc,
    case d.source_type
      when 'RA_10173' then 1 when 'DPA_IRR' then 2 when 'NPC_CIRCULAR' then 3
      when 'NPC_ADVISORY' then 4 when 'NPC_ADVISORY_OPINION' then 5
      when 'NPC_PUBLIC_ADVISORY' then 6 else 7
    end,
    d.publication_date desc nulls last
  limit least(greatest(match_count, 1), 50);
$$;

alter table public.documents enable row level security;
alter table public.document_chunks enable row level security;

-- No browser-facing policies are intentional: all access goes through FastAPI.
revoke all on table public.documents from anon, authenticated;
revoke all on table public.document_chunks from anon, authenticated;
revoke execute on function public.match_document_chunks(extensions.vector, double precision, integer, text[]) from public, anon, authenticated;
revoke execute on function public.search_document_chunks(text, integer, text[]) from public, anon, authenticated;

grant all on table public.documents to service_role;
grant all on table public.document_chunks to service_role;
grant execute on function public.match_document_chunks(extensions.vector, double precision, integer, text[]) to service_role;
grant execute on function public.search_document_chunks(text, integer, text[]) to service_role;

-- No storage.objects policies are created; only the service role can use this bucket.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'npc-documents', 'npc-documents', false, 26214400,
  array['application/pdf', 'text/plain']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

-- Expand the knowledge base from privacy-only materials to supported Philippine
-- digital, cybercrime, electronic-transactions, archives, and e-government laws.
-- Run after 202609020001_initial_knowledge_base.sql.

begin;

alter table public.documents
  drop constraint if exists documents_source_type_check;

alter table public.documents
  add constraint documents_source_type_check check (
    source_type ~ '^RA_[0-9]{4,5}$'
    or source_type in (
      'DPA_IRR', 'NPC_CIRCULAR', 'NPC_ADVISORY',
      'NPC_ADVISORY_OPINION', 'NPC_PUBLIC_ADVISORY', 'OTHER_NPC_ISSUANCE'
    )
  );

alter table public.documents
  drop constraint if exists documents_official_source;

alter table public.documents
  add constraint documents_official_source check (
    source_url is null
    or source_url ~ '^https://(([a-z0-9-]+\.)*privacy\.gov\.ph|elibrary\.judiciary\.gov\.ph)(/|$)'
  );

commit;

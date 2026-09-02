# Manual Supabase Setup

Supabase provides authentication, private conversation history, and the backend knowledge base. React uses only the project URL and publishable key for Auth and user-owned rows. FastAPI performs privileged knowledge-base, vector-search, and storage operations with the server-only secret key.

## 1. Create a Project

1. Sign in at `https://supabase.com/dashboard` and create a project.
2. Choose a strong database password and keep it in a password manager.
3. Select the nearest suitable region for the application's expected users.
4. Wait for provisioning to finish.

## 2. Apply the Migration

1. Open **SQL Editor** in the Supabase dashboard.
2. Open `supabase/migrations/202609020001_initial_knowledge_base.sql` locally.
3. Paste all of it into a new query and click **Run** once.
4. Run `202609030002_user_auth_and_conversations.sql` next.
5. Run `202609030003_expand_supported_digital_laws.sql` last.
6. Confirm each query completes without errors.

This creates the extensions, private document tables, 768-dimension vector index, full-text search, retrieval functions, RLS restrictions, and the private `npc-documents` bucket.

Use migration files for later schema changes. Once this workflow is adopted, avoid untracked production changes through the Table Editor.

## 3. Verify the Setup

Run this in SQL Editor:

```sql
select extname, extversion
from pg_extension
where extname in ('vector', 'pgcrypto');

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('documents', 'document_chunks');

select id, name, public, file_size_limit, allowed_mime_types
from storage.buckets
where id = 'npc-documents';
```

Both extensions and tables should exist. The bucket should have `public = false`. In **Database > Tables**, verify RLS is enabled on both tables and no policy grants access to `anon` or `authenticated`.

## 4. Configure FastAPI

Open **Project Settings > API** or **Connect** and collect:

- Project URL -> `SUPABASE_URL`
- Backend secret key (`sb_secret_...`) -> `SUPABASE_SECRET_KEY`

Create `backend/.env` from `backend/.env.example` and enter those values. Never put the secret key in `frontend/.env`, React code, browser storage, screenshots, or commits. It maps to the privileged `service_role`, bypasses RLS, and belongs only on FastAPI.

## 5. Configure Login

Follow `docs/auth-setup.md`. In **Authentication > Providers > Email**, keep signup enabled and disable **Confirm Email** to return a session immediately after registration.

For the single-owner admin workflow:

1. Generate a long random `ADMIN_API_KEY` and save it in `backend/.env`.
2. Require it on every `/api/admin/*` endpoint.
3. Store it only in the backend environment and the administrator's password manager.
4. Rate-limit admin routes and never log the key.

A future admin-role migration can replace this shared key with role-based admin authentication.

## 6. Keep Embeddings Matched

The migration uses `extensions.vector(768)`. FastAPI must request the same output size:

```env
EMBEDDING_MODEL=gemini-embedding-2
EMBEDDING_DIMENSION=768
```

Do not change one without migrating the other. Re-embed existing chunks when changing models or dimensions because embeddings from different models are not meaningfully comparable.

## 7. Ingest Official Documents

Wait for the ingestion endpoint instead of manually adding authoritative rows. The intended flow is:

1. FastAPI uploads an official PDF or text file to `npc-documents`.
2. It inserts metadata with `processing_status = 'pending'`.
3. It extracts and structurally chunks the text.
4. It creates one 768-dimension embedding per chunk.
5. It inserts all chunks, then marks the document `ready`.

Use official NPC URLs under `privacy.gov.ph` or statute copies hosted by the
Supreme Court E-Library under `elibrary.judiciary.gov.ph`. The migration rejects
other source domains when a URL is supplied. Supported statutory source types
use the `RA_<number>` format, such as `RA_10173` or `RA_7394`.

An explicitly named Republic Act does not have to be ingested before the chatbot
can answer from it. When Supabase does not contain that Act, FastAPI resolves the
exact number through the Supreme Court E-Library and retrieves the official page
for that request. This fallback is cached in backend memory and does not create
document or chunk rows automatically.

## 8. Pre-deployment Checklist

- The frontend has only `VITE_API_BASE_URL`.
- Supabase secrets exist only in the backend runtime environment.
- Browser roles cannot access tables, retrieval functions, or stored source files.
- FastAPI enforces CORS, request limits, rate limits, and the admin key.
- Production logs exclude full chat text, credentials, and tokens.

Official references: [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security), [vector columns](https://supabase.com/docs/guides/ai/vector-columns), [vector indexes](https://supabase.com/docs/guides/ai/vector-indexes), [Storage quickstart](https://supabase.com/docs/guides/storage/quickstart), and [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings).

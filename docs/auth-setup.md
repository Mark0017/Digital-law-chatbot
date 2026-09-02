# Supabase Login Setup

This setup uses Supabase email/password authentication. Users can register and receive a session immediately because email confirmation is disabled.

## 1. Apply the Auth Migration

After the knowledge-base migration succeeds, open Supabase **SQL Editor** and run:

```text
supabase/migrations/202609030002_user_auth_and_conversations.sql
```

It creates:

- A profile automatically for every new Auth user.
- Private conversations and messages.
- Row Level Security policies tied to `auth.uid()`.
- A rule allowing browser users to insert only messages with role `user`.
- Backend-only insertion of `assistant` messages through the secret key.

## 2. Enable Immediate Login After Signup

In the Supabase dashboard:

1. Open **Authentication > Providers**.
2. Open the **Email** provider.
3. Keep **Allow new users to sign up** enabled.
4. Disable **Confirm Email**.
5. Save the provider settings.

With Confirm Email disabled, Supabase implicitly confirms the address and returns both a user and session after a successful signup. This behavior cannot be configured by the application database migration on a hosted project.

Disabling confirmation allows registration with an email address the person may not own. Before public deployment, enable CAPTCHA and rate limiting to reduce automated and impersonation signups.

## 3. Configure the Frontend

Add these values to `frontend/.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
```

The publishable key is intended for frontend use. Never put `SUPABASE_SECRET_KEY`, `GEMINI_API_KEY`, or `ADMIN_API_KEY` in the frontend.

## 4. Expected Signup Flow

The React application will call `supabase.auth.signUp()` with an email and password. A successful response contains an active session, the profile trigger creates `public.profiles`, and the user can enter the chatbot immediately without opening an email.

Official references: [Supabase Auth configuration](https://supabase.com/docs/guides/auth/general-configuration), [user management](https://supabase.com/docs/guides/auth/managing-user-data), and [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).


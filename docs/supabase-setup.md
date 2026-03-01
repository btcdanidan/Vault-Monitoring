# Supabase Auth Project Setup & Configuration

This guide covers creating and configuring a Supabase project for the DeFi Vault Intelligence Platform. Supabase is used **auth only** (no Realtime, Storage, or Edge Functions). All application data lives in the self-hosted PostgreSQL + TimescaleDB instance. See §19 (Auth & Multi-user) and §10 (System Architecture).

---

## 1. Create the Supabase Project

### Option A: Supabase Dashboard (manual)

1. Go to [Supabase Dashboard](https://supabase.com/dashboard) and sign in.
2. Click **New Project**.
3. Choose your **Organization** (e.g. your personal org).
4. Set **Name** to `defi-vault` (or similar).
5. Set **Database Password** and store it securely (used for direct DB access; app uses env vars).
6. Choose **Region**: `eu-central-1` (Frankfurt) recommended for Hetzner EU proximity.
7. Click **Create new project** and wait until status is **Active**.

### Option B: Supabase MCP (when available)

If using the Supabase MCP:

1. Call `get_cost` with `type: "project"` and your `organization_id`.
2. Call `confirm_cost` with the returned cost (free tier: 0).
3. Call `create_project` with:
   - `name`: `defi-vault`
   - `region`: `eu-central-1`
   - `organization_id`: your org ID
   - `confirm_cost_id`: from step 2
4. Poll `get_project` with the new project ID until `status` is `ACTIVE_HEALTHY`.

---

## 2. Retrieve Credentials

After the project is active, collect these four values and add them to your `.env` (never commit real values).

| Variable | Where to find it |
|---------|------------------|
| `SUPABASE_URL` | Dashboard → Project Settings → API → **Project URL** (e.g. `https://xxxxx.supabase.co`) |
| `SUPABASE_ANON_KEY` | Dashboard → Project Settings → API → **Project API keys** → `anon` **public** key |
| `SUPABASE_JWT_SECRET` | Dashboard → Project Settings → API → **JWT Settings** → **JWT Secret** |
| `SUPABASE_SERVICE_ROLE_KEY` | Dashboard → Project Settings → API → **Project API keys** → `service_role` **secret** key |

For the frontend you also need (same values, public only):

- `NEXT_PUBLIC_SUPABASE_URL` = same as `SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` = same as `SUPABASE_ANON_KEY`

**Security:** Never expose `SUPABASE_JWT_SECRET` or `SUPABASE_SERVICE_ROLE_KEY` to the frontend. Use them only in the FastAPI backend.

---

## 3. Auth Provider Configuration (Dashboard)

### 3.1 Enable email/password and magic link

1. In the Supabase Dashboard, open your project.
2. Go to **Authentication** → **Providers**.
3. **Email** should be enabled by default.
4. Under Email provider settings, enable **Magic Link** (passwordless sign-in option).
5. Save.

### 3.2 Password requirement (min 8 characters)

1. Go to **Authentication** → **Providers** → **Email** (or **Settings** depending on UI).
2. Find **Password Requirements** or **Minimum password length**.
3. Set minimum length to **8 characters** (Supabase default is often 6; ensure it is at least 8 per ticket).
4. Save.

### 3.3 Email templates (optional branding)

1. Go to **Authentication** → **Email Templates**.
2. **Confirm signup** — Customize subject/body for “Confirm your signup” (e.g. mention “DeFi Vault Intelligence Platform”).
3. **Magic Link** — Customize subject/body for magic link sign-in.
4. **Invite** (if used later for approval flow) — Customize for admin-invited users.

Leave template variables (e.g. `{{ .ConfirmationURL }}`) intact; only change branding text.

---

## 4. Disable Non-Auth Features (auth-only usage)

We use Supabase for **auth only**. Disable or avoid:

| Feature | Action |
|--------|--------|
| **Realtime** | Database → Replication: do not add app tables to the publication, or remove all if any were added. |
| **Storage** | Do not create buckets. No action if none exist. |
| **Edge Functions** | Do not deploy any. No action if none exist. |

No need to “disable” these in a special way; simply do not use them.

---

## 5. Verify Auth Schema (optional)

To confirm the auth schema is present and operational, run this in the Supabase SQL Editor (Dashboard → SQL Editor) or via MCP `execute_sql` with your `project_id`:

```sql
-- Check auth schema and core tables exist
SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'auth';
SELECT table_name FROM information_schema.tables WHERE table_schema = 'auth' ORDER BY table_name;
```

You should see the `auth` schema and tables such as `users`, `sessions`, etc. No changes are required; this is diagnostic only.

---

## 6. Environment and Docker

- Copy `.env.example` to `.env` and fill in the four Supabase variables (and the two `NEXT_PUBLIC_*` if running the frontend).
- For Docker, `infra/docker-compose.yml` passes `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` to the frontend service; ensure these are set in `.env` (or in the compose `environment` section) when running the stack.

---

## 7. Checklist

- [ ] Supabase project created (Dashboard or MCP).
- [ ] `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY` in `.env`.
- [ ] `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in `.env` for frontend.
- [ ] Email provider enabled; Magic Link enabled.
- [ ] Minimum password length set to 8 characters.
- [ ] Email templates updated (optional).
- [ ] Realtime/Storage/Edge Functions not used (auth only).
- [ ] Optional: Ran auth schema verification SQL.

Once this is done, the backend can use JWT validation with `SUPABASE_JWT_SECRET`, and the frontend can use `@supabase/supabase-js` with the public URL and anon key (see §19 and follow-up S02 tickets).

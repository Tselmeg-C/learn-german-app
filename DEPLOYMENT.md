# Deployment

Three services: Supabase (Postgres + Auth), Fly.io (API), Cloudflare Pages (web).

Everything below needs accounts and credentials, so it is written to be followed rather
than automated. The API image and `fly.toml` are ready — the image has been built and run
against Postgres, serving as a non-root user with its healthcheck passing.

## 1. Supabase

1. Create a project at <https://supabase.com/dashboard>. Note the **project ref** (the
   `abcdefghijklmnop` in `https://abcdefghijklmnop.supabase.co`).
2. **Authentication → Sign In / Providers**: enable **Email** (magic link) and **Google**.
3. **Authentication → URL Configuration**: set the Site URL to your web origin and add it
   to Redirect URLs. Without this, sign-in links bounce to localhost.
4. **Settings → API**: copy the **anon** key. It is safe in the browser — it is not a
   secret. The **service_role** key is, and this app never needs it.
5. **Settings → Database**: copy the connection string. Use the **Session pooler** URI
   (port 5432 via Supavisor), not the direct connection.

The API verifies JWTs against `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`,
so no JWT secret is shared with us.

### The database URL

SQLAlchemy needs the async driver, so rewrite the scheme Supabase gives you:

```
postgresql://...        ->  postgresql+asyncpg://...
```

## 2. API on Fly.io

```bash
cd api
fly launch --no-deploy --copy-config     # edit app name/region in fly.toml first

fly secrets set \
  LGAPP_DATABASE_URL='postgresql+asyncpg://postgres.<ref>:<password>@<host>:5432/postgres' \
  LGAPP_SUPABASE_PROJECT_REF='<ref>' \
  LGAPP_CORS_ORIGINS='["https://your-web-origin"]'

fly deploy
```

`fly.toml` runs `alembic upgrade head` as the release command, so migrations apply once
per deploy rather than once per machine — several machines booting at the same time would
otherwise race to migrate the same database.

Verify:

```bash
curl https://<app>.fly.dev/healthz   # liveness
curl https://<app>.fly.dev/readyz    # also proves it can reach Postgres
```

### Scaling

The API is stateless, so scaling is `fly scale count 3`. Two things matter more than the
machine count:

- **Use the pooler URL.** Each machine holds up to `LGAPP_DB_POOL_SIZE + LGAPP_DB_MAX_OVERFLOW`
  connections (10 by default). Postgres runs out of connections long before it runs out of
  CPU, and Supavisor is what stops that being your scaling limit.
- **Watch the due-queue query.** It is an index scan on `(user_id, due)`. If that ever
  shows up as a sequential scan, the index has been lost, not outgrown.

## 3. Web on Cloudflare Pages

Connect the repo and configure:

| Setting | Value |
|---|---|
| Build command | `npm run build` |
| Build output | `dist` |
| Root directory | `web` |

Environment variables:

```
VITE_SUPABASE_URL=https://<ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon key>
VITE_API_URL=https://<app>.fly.dev
```

Only `VITE_`-prefixed variables reach the client, and everything that does is public —
never put a service-role key here.

Once `VITE_API_URL` points at another origin, the API's `LGAPP_CORS_ORIGINS` must list the
web origin exactly, scheme included.

## 4. Load the content

```bash
cd api
LGAPP_DATABASE_URL='postgresql+asyncpg://...' \
  uv run lgapp import-content ../seed/a1-basics.csv --deck a1-basics --name "A1 Basics"
```

Re-running is a no-op, so this is safe to repeat. Use `--dry-run` first against production
to see what a file would change before it changes it.

## Environment variables

### API

| Variable | Required | Notes |
|---|---|---|
| `LGAPP_DATABASE_URL` | yes | Must use `postgresql+asyncpg://`. Prefer the pooler host. |
| `LGAPP_SUPABASE_PROJECT_REF` | yes | Derives the JWKS URL and the expected token issuer. |
| `LGAPP_CORS_ORIGINS` | yes | JSON array of exact origins. |
| `LGAPP_ENV` | no | `production` in deployment. |
| `LGAPP_LOG_LEVEL` | no | Defaults to `INFO`. Logs are JSON with a `request_id`. |
| `LGAPP_DB_POOL_SIZE` | no | Per process. Keep small behind a pooler. |

### Web

| Variable | Required | Notes |
|---|---|---|
| `VITE_SUPABASE_URL` | yes | |
| `VITE_SUPABASE_ANON_KEY` | yes | Public by design. |
| `VITE_API_URL` | yes | Empty in development; Vite proxies `/v1` instead. |

## Operational notes

- **Tracing a request.** Every log line carries `request_id`, and responses echo it in
  `x-request-id`. Pass your own to follow a call end to end.
- **A learner reports a wrong schedule.** `review_logs` is the source of truth and
  `user_cards` is derived from it, so the card can always be rebuilt by replaying the log.
  Nothing is lost.
- **`import_batches`** records every content import with a checksum, so "when did this card
  change?" is answerable.

# learn-german-app

An app providing a supremum German learning experience — spaced-repetition vocabulary
review, usable on phone and desktop.

## Layout

| Path | What it is |
|---|---|
| `api/` | FastAPI service (Python 3.12, `uv`) — the only thing that touches the database |
| `web/` | React + TypeScript PWA (installable, offline review) |
| `seed/` | Starter deck, loaded through the same importer real content will use |
| `docker-compose.yml` | Local Postgres |

## Architecture

- **Postgres + Supabase Auth.** The API is stateless: every request is authorized from a
  Supabase JWT verified locally against their JWKS endpoint, and all state lives in
  Postgres. Scaling out is adding machines — no session affinity.
- **`review_logs` is the source of truth**; the per-card FSRS state in `user_cards` is a
  derived cache of the replayed log. This is what makes offline sync correct: FSRS is
  order-dependent, and offline reviews arrive late, so a review that predates the card's
  last review triggers a replay rather than being applied on top of newer state.
- **Content is re-importable.** Cards key on `(deck_id, external_id)`, so re-running an
  import is idempotent and never touches user progress.

## Local development

Requires [uv](https://docs.astral.sh/uv/), Node 22+, and Docker.

```bash
docker compose up -d                 # Postgres on :5432

# API
cd api
cp ../.env.example .env
uv sync
uv run alembic upgrade head
uv run lgapp import-content ../seed/a1-basics.csv --deck a1-basics --name "A1 Basics"
uv run uvicorn lgapp.main:app --reload

# Web, in another shell
cd web
cp .env.example .env.local           # needs a Supabase project for sign-in
npm install
npm run dev
```

- Web: <http://localhost:5173> — Vite proxies `/v1` to the API, so the client is same-origin.
- API: <http://localhost:8000> — interactive docs at `/docs`.
- `GET /healthz` is liveness; `GET /readyz` also round-trips the database.

Sign-in needs a Supabase project — see [DEPLOYMENT.md](./DEPLOYMENT.md).

## Checks

CI runs exactly these; the API tests use a real Postgres.

```bash
cd api
uv run ruff check . && uv run ruff format --check .
uv run mypy src              # strict on services/
uv run pytest

cd ../web
npm run lint
npx tsc -b --noEmit
npm test
```

## Content

Cards load through one importer, which the seed deck uses too — there is no separate
seeding path to rot.

```bash
cd api
uv run lgapp import-content <file.csv|file.json> --deck <slug> [--name "Name"] [--dry-run]
```

It is idempotent: re-running an unchanged file reports `0 new, 0 updated` and writes
nothing. Every run is recorded in `import_batches` with a checksum. Validation errors are
reported all at once, with line numbers, and nothing is written unless every row is valid.

The web client's types are generated from the API's OpenAPI document. After changing a
response model:

```bash
cd web && npm run gen:api    # CI fails if this leaves a diff
```

## Conventions

- **Migrations only.** Schema changes go through Alembic; no `create_all` outside tests.
- **Tests run on real Postgres**, not SQLite — we depend on Postgres-specific upserts.
- **Config is env-driven** via `pydantic-settings`, prefix `LGAPP_`. No secrets in the
  repo; `.env.example` documents every variable.
- **Errors are RFC 9457** problem+json. Logs are JSON, carrying `request_id` — pass
  `x-request-id` to trace a call through.
- **FSRS fuzzing is off, deliberately.** It randomises intervals, which would make
  scheduling non-deterministic and let `user_cards` drift from the log it is derived
  from. See the comment in `api/src/lgapp/services/scheduler.py`.

## Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md). Supabase for Postgres and Auth, Fly.io for the API,
Cloudflare Pages for the web client.

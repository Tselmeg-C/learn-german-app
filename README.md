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

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
docker compose up -d                 # Postgres on :5432
cd api
cp ../.env.example .env              # fill in Supabase values when auth is wired up
uv sync
uv run uvicorn lgapp.main:app --reload
```

- API: <http://localhost:8000> — interactive docs at `/docs`
- `GET /healthz` is liveness; `GET /readyz` also round-trips the database.

## Checks

Run from `api/`. CI runs exactly these against a real Postgres.

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src              # typecheck (strict on services/)
uv run pytest                # tests
```

## Conventions

- **Migrations only.** Schema changes go through Alembic; no `create_all` outside tests.
- **Tests run on real Postgres**, not SQLite — we depend on Postgres-specific upserts.
- **Config is env-driven** via `pydantic-settings`, prefix `LGAPP_`. No secrets in the
  repo; `.env.example` documents every variable.
- **Errors are RFC 9457** problem+json. Logs are JSON, carrying `request_id` — pass
  `x-request-id` to trace a call through.

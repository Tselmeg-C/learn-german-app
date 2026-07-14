# Where this stands

Last worked: **2026-07-14**. Everything below is committed and pushed to `main`.

## Status

| | |
|---|---|
| API (FastAPI + Postgres) | **Done.** 118 tests, verified end-to-end over HTTP against real Postgres. |
| Web (React PWA) | **Done.** 30 tests, driven in a real browser: decks → review → grade → stats. |
| Offline review | **Done.** Durable IndexedDB outbox, idempotent sync. |
| Content importer + 50-card seed deck | **Done.** Idempotent; re-running an unchanged file writes nothing. |
| **Deploy** | **Not started — needs your accounts.** See `DEPLOYMENT.md`. |

## Next step

**Create the Supabase project.** It blocks everything else: sign-in is the only thing
standing between you and clicking through the real app with real data. ~10 minutes,
walkthrough in `DEPLOYMENT.md` §1.

Two things there that will bite:
- Rewrite the connection string to `postgresql+asyncpg://` — SQLAlchemy needs the async driver.
- Use the **Session pooler** host, not the direct one.

Then: Fly.io for the API (`DEPLOYMENT.md` §2), Cloudflare Pages for the web (§3).

## Open decisions (yours to make)

1. **FSRS fuzzing is off.** It randomises intervals to stop cards clumping, but it makes
   scheduling non-deterministic, which would let `user_cards` drift from the `review_logs`
   it is derived from. Measured: identical input → due dates across 5 different days.
   Anti-clumping can come back as a jitter derived from each review's UUID (stable under
   replay) if clumping ever actually annoys you. Rationale is in
   `api/src/lgapp/services/scheduler.py`.
2. **Daily new-card limit: 20/deck.** Arbitrary default, per-user column, easy to change.
3. **Dev auth bypass?** Offered but not built — would let you click through the real loop
   before Supabase exists. It is a deliberate hole in the auth wall, so it needs your
   explicit yes and an env-var fence.

## Resume in one paste

```bash
docker compose up -d
cd api && uv sync && uv run alembic upgrade head
uv run lgapp import-content ../seed/a1-basics.csv --deck a1-basics --name "A1 Basics"
uv run uvicorn lgapp.main:app --reload          # :8000, docs at /docs

cd ../web && npm install && npm run dev         # :5173, proxies /v1 to the API
```

Without Supabase configured you will see the login page and stop there.

## The one idea to reload into your head

`review_logs` is the **source of truth**; `user_cards` is a **cache** of what replaying
that log produces. Everything else follows from it:

- FSRS is order-dependent, and offline reviews arrive late. A review newer than the card's
  `last_review` steps the cache forward (cheap); anything older **rebuilds the card from
  its log**, because applying an old review on top of newer state corrupts the schedule.
- Reviews carry a client-generated UUIDv7. That is the idempotency key — a retry after a
  timeout is absorbed, not double-counted. This is why the client can retry freely.
- Scheduling must therefore be deterministic. Hence no fuzzing.

Live in `api/src/lgapp/services/{scheduler,reviews}.py`. The tests that pin it are
`TestOfflineSync` in `api/tests/test_api.py` — mutation-checked, so they genuinely fail if
the replay logic breaks.

## Traps already hit (don't re-discover these)

- **Alembic downgrade leaves Postgres ENUM types behind** → re-upgrade fails on "type
  already exists". Fixed with explicit drops; guarded by `api/tests/test_migrations.py`.
- **`migrations/env.py` must not override an explicitly-passed database URL** — it did,
  and the test suite silently ran against the *dev* database.
- **The Vite template ships without `strict`.** Now enabled. It immediately caught
  `retention_rate` being optional, which would have rendered "NaN%".
- **An API 401/500 must not render as "Nothing due right now."** Only offline-with-empty-cache
  means "nothing due". Unit tests all passed while this was broken — it took driving the
  real app to find.
- **Typer collapses a single-command app**, making `lgapp import-content` unreachable. A
  root callback keeps subcommands addressable.

## Conventions worth keeping

- Migrations only; no `create_all` outside tests.
- API tests run on **real Postgres** (we rely on Postgres-specific upserts), each in a
  rolled-back transaction.
- Web types are **generated** from the API's OpenAPI doc: `cd web && npm run gen:api`.
  CI fails on drift.
- Content and user state are separate tables. An import must never touch a learner's
  progress.

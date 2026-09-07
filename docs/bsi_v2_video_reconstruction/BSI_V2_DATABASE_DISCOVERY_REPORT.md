# BSI V2 Database Discovery Report

Date: 2026-09-03

## Verdict

The existing Bensim historical database was located and read-only access works.

## Actual Database

- Backend: PostgreSQL 16 Docker service
- Container: `openterminalui-postgres-1`
- Compose service: `postgres`
- Host port: `127.0.0.1:5432`
- Container port: `5432`
- Database: `openterminalui`
- User: `openterminalui`
- Data volume: `openterminalui_postgres_recovered_20260807`
- Credentials: supplied by Docker Compose / `.env`; password masked in all reports.

## Why This Shell Had No DATABASE_URL

The Codex shell was running outside the Docker Compose backend container.

Inside the backend container, `DATABASE_URL` is injected by `docker-compose.yml`.
Outside the container, the shell had neither `DATABASE_URL` nor `TEST_DATABASE_URL`.

This was an environment boundary, not absence of a historical database.

## Runtime Configuration Path

`docker-compose.yml` injects:

`DATABASE_URL=${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER:-openterminalui}:***@postgres:5432/${POSTGRES_DB:-openterminalui}}`

`backend/db/base.py` resolves it through:

- `load_local_env()`
- `get_settings()`
- `DATABASE_URL` when present
- fallback SQLite only when `DATABASE_URL` is absent

`backend/shared/db.py` creates the sync SQLAlchemy engine from `get_sync_database_url()`.

## Existing Historical Tables

Confirmed present:

- `mt5_canonical_candles`
- `mt5_candle_revisions`
- `mt5_decision_snapshots`
- `historical_pattern_fingerprints`
- `historical_setup_outcomes`
- `historical_replay_runs`
- `historical_replay_parity_checks`
- `bsi_thesis_records`

Estimated row counts:

- `mt5_canonical_candles`: ~6,476,285
- `mt5_candle_revisions`: ~5,105,043
- `historical_pattern_fingerprints`: ~1,779,479
- `historical_setup_outcomes`: ~1,990,862
- `bsi_thesis_records`: ~3,134

## Previous BSI V1 Backfill Path

`run_bsi_backfill.py` documents the prior BSI historical validation path.

It requires the real Postgres corpus through `DATABASE_URL`, reads:

- `mt5_canonical_candles`
- `mt5_candle_revisions`

and writes V1 subtype results to:

- `historical_pattern_fingerprints`
- `historical_setup_outcomes`

with pseudo strategy ids shaped as:

- `bsi__bsi_order_flow`
- `bsi__bsi_abc`
- `bsi__bsi_asian`
- `bsi__bsi_new_york`
- `bsi__bsi_under_over`
- `bsi__bsi_0930`
- `bsi__bsi_abcd`
- `bsi__bsi_reactionary`
- `bsi__bsi_ob_liquidity`

## Existing V1 BSI Corpus

Resolved V1 rows found:

- `bsi__bsi_0930`: 3
- `bsi__bsi_abc`: 4
- `bsi__bsi_abcd`: 178
- `bsi__bsi_asian`: 14
- `bsi__bsi_new_york`: 3,266
- `bsi__bsi_order_flow`: 28
- `bsi__bsi_under_over`: 502

No existing V1 rows were found for `bsi__bsi_reactionary` or `bsi__bsi_ob_liquidity` in the grouped query.

## Safety Decision

All discovery was read-only SQL.

No tables were altered.
No V1 rows were deleted or modified.
No DEMO/live setting was changed.

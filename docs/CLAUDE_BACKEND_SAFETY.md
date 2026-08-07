# Backend/Database Safety Workflow

Standing procedure since the 2026-08-07 incident: a pytest fixture ran
`Base.metadata.drop_all(bind=engine)` against `backend.shared.db.engine` — the
same engine the live application uses via `DATABASE_URL` — and wiped almost every
table in the shared development database, with no backup to recover from. Full
incident record: `db_incidents` table (migration `0036_db_incident_boundary`).

This document is the mandatory checklist for Claude (or anyone) before doing
significant backend, database, trading-engine, Adaptive Manager, Portfolio
Manager, MT5, economic-intelligence, learning-engine, migration, or test work in
this repository.

## The three environments — never confuse them

| | Purpose | Database | Destructive tests allowed? |
|---|---|---|---|
| **A. Development database** | Real application state: Adaptive Manager history, Portfolio history, economic intelligence, learning data, MT5 recovered trades | Current recovered `openterminalui` DB (`openterminalui_postgres_recovered_20260807` volume) | **Never.** Never `Base.metadata.drop_all()`. Never used as `TEST_DATABASE_URL`. |
| **B. Test database** | Automated backend tests | Ephemeral `openterminalui_test` (`docker-compose.test.yml`), `tmpfs`-backed, destroyed after use | Yes — that's what it's for. |
| **C. MT5 demo trading account** | Real broker-side trading development/validation | `INTERNAL_DEMO`, `MetaQuotes-Demo`, profile `internal_demo_10k` | Only this account, ever, for development. |

`PERSONAL_LIVE` and `PROP_*` accounts are never used for development or testing,
until explicitly promoted through a separate controlled process outside this
workflow.

## Mandatory steps before significant backend work

1. `git status` — know what's already dirty before you start.
2. Note the current branch.
3. Back up the development database: `.\scripts\backup_postgres.ps1` (or
   `.\scripts\pre_backend_task.ps1 -TaskName "<short-name>"`, which does this plus
   records git/Alembic state and only proceeds if the backup verifies).
4. Verify the dump: `pg_restore --list` (done automatically by the backup script —
   confirm it printed `backup_verified=true`).
5. Confirm `TEST_DATABASE_URL` is safe before running any test that touches a real
   database (see `backend/shared/test_db_safety.py` — this is enforced
   automatically by `backend/tests/conftest.py`'s `pytest_configure` hook, which
   aborts the whole session before collection if it isn't).
6. Do the actual work.
7. Run focused tests for what you changed.
8. Run the broad/full suite **only** against the ephemeral test Postgres:
   ```
   docker compose -f docker-compose.test.yml up -d postgres_test
   # migrate it, run tests with DATABASE_URL/TEST_DATABASE_URL pointed at it
   docker compose -f docker-compose.test.yml down -v
   ```
9. Apply an Alembic migration only after a pre-migration backup:
   `.\scripts\pre_migration_backup.ps1` (backup → verify → `alembic upgrade head`;
   aborts before migrating if the backup step fails and `-RequireBackup` is set,
   which is the default).
10. Rebuild only the service that actually needs it (usually `docker compose build
    backend`) — never rebuild frontend or recreate Postgres/Redis unless the task
    explicitly requires it.
11. Verify health: container status, `/health`, relevant manager startup log lines
    (Portfolio manager, Adaptive trade manager, Economic intelligence scheduler,
    MT5 autonomous scheduler), no `does not exist` / `UndefinedTable` errors.
12. Commit in focused, logically-scoped commits — never bundle unrelated work.

**Never run destructive tests against the development database.** If you are ever
unsure whether an operation is destructive, treat it as destructive.

## Backup mechanics

- `scripts/backup_postgres.ps1` — pg_dump custom format (`-Fc`), written outside
  the Docker volume and outside the git repo (default:
  `C:\Users\Intel\Desktop\BensimBackups`), verified with `pg_restore --list`, plus
  a `*.metadata.json` sidecar (git branch/commit/dirty flag, Alembic revision, DB
  name, Docker volume name, Postgres version, app environment — **never**
  credentials). Retention: `daily` category prunes anything older than
  `-PruneDays` (default 7); `premigration` keeps the last 10; `manual` and
  `incident` are never auto-pruned.
- `scripts/restore_postgres.ps1` — restores **only** into a brand-new database it
  creates itself; refuses protected names (`openterminalui`, `production`,
  `staging`, `development`, `bensim`) and refuses to restore over an
  already-existing database. Never touches the source.
- `scripts/pre_migration_backup.ps1` — backup → verify → `alembic upgrade head`,
  in that order, always. Never migrate first.
- `scripts/pre_backend_task.ps1 -TaskName "..."` — the all-in-one preflight;
  prints `SAFE TO BEGIN BACKEND TASK` only on success.
- `GET /api/system/backup-status` — read-only observability: last verified backup
  timestamp/age, current Alembic revision, git commit at backup time. `WARN` if
  the last verified backup is older than 24h, `CRITICAL` if older than 72h. Does
  not block trading by itself.

### Daily backup scheduling (not created automatically — see below)

To schedule `backup_postgres.ps1` to run automatically once a day, use Windows
Task Scheduler with:

```
Program/script:  powershell.exe
Arguments:       -ExecutionPolicy Bypass -File "C:\Users\Intel\Desktop\Devops\trading\OpenTerminalUI\scripts\backup_postgres.ps1"
Trigger:         Daily, at a time the PC is normally on
```

Or via `schtasks` (run manually, once, with explicit approval — this workflow
document does not create the scheduled task on its own):

```
schtasks /Create /SC DAILY /TN "BensimPostgresBackup" /TR "powershell.exe -ExecutionPolicy Bypass -File \"C:\Users\Intel\Desktop\Devops\trading\OpenTerminalUI\scripts\backup_postgres.ps1\"" /ST 20:00
```

## Testing mechanics

### Unit tests
Prefer fake repositories, a fake MT5 adapter, in-memory SQLite where a DB is
needed, deterministic fixtures, mocks. Should not require a real MT5 terminal,
real MetaQuotes demo account, real dev Postgres, or a real OpenAI call — unless
explicitly marked as an integration test.

### Database integration tests
Only against the ephemeral test Postgres (`docker-compose.test.yml`,
`openterminalui_test`). Migrations, ORM, persistence, reconciliation, execution
journals, learning tables, economic tables, account registry — all fair game
there; all destructive cleanup stays inside that disposable database.

### MT5 tests — two categories
- **`MT5_OFFLINE_TEST`** (`@pytest.mark.mt5_offline`): fake bridge, fake broker
  responses, fixture symbol metadata, simulated `order_calc_profit`, deterministic
  fake ticks. Run automatically, no real broker involved.
- **`MT5_DEMO_INTEGRATION_TEST`** (`@pytest.mark.mt5_demo_integration`): the real,
  currently-connected `INTERNAL_DEMO` `MetaQuotes-Demo` account. Must be
  explicitly marked. May perform `account_info`/`terminal_info`/`symbol_info`/
  ticks/history/positions queries and dry-run decisions freely. Any broker
  *mutation* (order/SL/TP/partial/close) requires the gate below and must route
  through `ExecutionManager` — never a direct `order_send()` from a test or
  script.

### Demo broker mutation gate
A real demo order/SL/TP/partial/close test may execute only when **all** of:
account classification `== INTERNAL_DEMO`, server/account fingerprint matches the
registered demo account, MT5 demo testing enabled, live trading disabled, prop
trading disabled, Execution Manager healthy, Portfolio Manager healthy,
economic/risk gates permit the action, and the operation is idempotent. If the
connected account ever changes to `PROP_EVALUATION`, `PROP_FUNDED`,
`PERSONAL_LIVE`, or `UNKNOWN`, automated test-mode broker mutations must fail
closed with `TEST_MUTATION_NON_DEMO_ACCOUNT` or
`TEST_MUTATION_ACCOUNT_FINGERPRINT_MISMATCH` — an env variable alone can never
override this.

### Before running the full suite
Print, and verify before collection:
```
TEST DATABASE:      openterminalui_test
DEVELOPMENT DATABASE: NOT TARGETED
```
If `TEST_DATABASE_URL` is missing, equals `DATABASE_URL`, targets a protected
name, or has no test marker, **abort the entire suite** — this is enforced by
`backend/tests/conftest.py`'s `pytest_configure` hook, backed by
`backend/shared/test_db_safety.py`.

### Engineering test trades vs. learning data
Explicitly-triggered engineering/test orders must be labeled `ENGINEERING_TEST`
and are excluded by default from strategy expectancy, policy learning,
entry-quality statistics, Adaptive Manager promotion, the probability engine, and
the strategy leaderboard. Normal autonomous demo trades on `INTERNAL_DEMO` remain
valid learning data and should keep feeding Adaptive Manager analytics, the
learning engine, the probability engine, the replay engine, strategy performance,
MFE/MAE analysis, and management-quality analytics — provided they aren't
contaminated or marked as an engineering test.

### Broker history as a secondary recovery layer
Because MT5 broker-side history survives even when the local database doesn't
(see `mt5_broker_recovered_trades`, `backend/scripts/recover_mt5_broker_history.py`),
periodically reconcile it into Bensim. This is a *secondary* recovery layer — it
does not replace database backups.

# Database

## Runtime

The default database is SQLite. Docker uses:

- `DATABASE_URL=sqlite+aiosqlite:////data/openterminal.db` unless overridden.
- `OPENTERMINALUI_SQLITE_URL` is also supported.

Optional PostgreSQL exists in `docker-compose.yml` under the `postgres` profile.

## SQLAlchemy Setup

- Sync engine/session: `backend/shared/db.py`.
- Async engine/session: `backend/db/base.py`, `backend/db/session.py`.
- Base metadata: `backend.shared.db.Base`.
- Startup calls `init_db()` and Alembic migrations are run by the container entrypoint.
- `init_db()` also contains compatibility column-add helpers for news sentiment, backtest columns, PIT fundamentals, and alert delivery fields.

## Migrations

Alembic versions currently present:

- `0001_initial`
- `0002_model_lab`
- `0003_portfolio_lab`
- `0004_institutional_risk_ops`
- `0005_news_sentiment_columns`
- `0006_trade_journal`
- `0006_notifications`
- `0007_alerts_v2_delivery`
- `0008_pit_fundamentals_release_metadata`
- `0011_saved_views`

Docker logs confirmed migrations ran and an initial admin user was seeded.

## Tables Observed

Main `backend/models/core.py` tables:

- holdings, tax_lots, watchlists, watchlist_items, insider_trades
- alert_rules, alert_history, alerts, alert_triggers
- future_contracts, nse_fno_bhavcopy
- news_articles
- backtest_runs
- model_experiments, model_runs, model_run_metrics, model_run_timeseries, model_registry
- portfolio_definitions, strategy_blends, portfolio_runs, portfolio_run_metrics, portfolio_run_timeseries, portfolio_run_matrices
- portfolio_mutual_funds, portfolios, portfolio_holdings, portfolio_transactions
- scan_presets, scan_runs, scan_results, scan_alert_rules, user_screens, saved_formulas
- data_versions, corp_actions, prices_eod, fundamentals_pit, universe_membership
- orders, fills, restricted_list, audit_log, ops_kill_switches
- virtual_portfolios, virtual_positions, virtual_orders, virtual_trades
- chart_drawings, chart_templates, user_layouts

Additional model files:

- `users`, `refresh_tokens`
- `journal_entries`
- `notifications`
- `saved_views`
- `portfolio_backtest_jobs`
- `experiments`, `experiment_artifacts`
- `instrument_master`

## Observations

- Some fields use JSON columns for flexible payloads.
- Some legacy tables store timestamps as strings while newer tables use `DateTime`.
- There are overlapping portfolio/watchlist concepts: legacy holdings/watchlist tables, newer multi-watchlist tables, paper trading virtual tables, and portfolio lab tables.
- Several model modules are imported through `backend/models/__init__.py`, while a top-level `models/` package also exists. This name collision blocks full backend pytest collection in the current Docker test environment.


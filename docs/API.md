# API

The running backend exposes 402 OpenAPI paths and 447 operations.

## Core Groups

- Health: `/health`, `/healthz`, `/metrics-lite`
- Auth: `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/forgot-access`
- Stocks/quotes/charts: `/api/stocks`, `/api/stocks/{ticker}`, `/api/quotes`, `/api/chart/{ticker}`, `/api/charts/*`
- Screener: `/api/screener/*`, `/api/v1/screener/*`
- Watchlists: `/api/watchlists`
- Portfolio: `/api/portfolio/*`, `/api/portfolios/*`
- Paper trading: `/api/paper/*`
- Alerts: `/api/alerts/*`, `/api/ws/alerts`
- News: `/api/news/*`
- FNO/options: `/api/fno/*`, `/api/options/*`
- Risk: `/api/risk/*`
- OMS/Ops: `/api/oms/*`, `/api/ops/*`
- Backtests/model lab: `/api/backtests/*`, `/api/model-lab/*`
- Portfolio lab/optimizer: `/api/portfolio-lab/*`, `/api/portfolio-optimizer/*`
- Research/AI/agent: `/api/research/*`, `/api/research-autopilot/*`, `/api/ai/*`, `/api/agent/*`
- Fixed income/bonds/economics/forex/commodities/crypto/ETF: `/api/fixed-income/*`, `/api/bonds/*`, `/api/economics/*`, `/api/forex/*`, `/api/commodities/*`, `/api/crypto/*`, `/api/etf/*`
- Forex: `/api/forex/instruments`, `/api/forex/instruments/{symbol}`, `/api/forex/quotes`, `/api/forex/quotes/{symbol}`, `/api/forex/candles/{symbol}`, `/api/forex/cross-rates`, `/api/forex/central-banks`. Canonical symbols are `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `USDCAD`, `AUDUSD`, `NZDUSD`, `XAUUSD`; provider symbols such as Yahoo `=X` mappings and `GC=F` stay provider-side. `XAUUSD` responses expose proxy metadata.
- Forex intelligence: `/api/forex-intelligence/analyze`, `/api/forex-intelligence/{symbol}`, `/api/forex-intelligence/{symbol}/latest`, `/api/forex-intelligence/{symbol}/history`, `/api/forex-intelligence/{symbol}/features`, `/api/forex-intelligence/features/{feature_vector_id}`, `/api/forex-intelligence/{symbol}/overlays` provide read-only forex and metals market context, persisted feature vectors, confluence and deterministic trade explanations.
- Forex frameworks: `/api/forex-frameworks`, `/api/forex-frameworks/{framework_id}`, `/api/forex-frameworks/analyze`, `/api/forex-frameworks/{symbol}/latest`, `/api/forex-frameworks/{symbol}/history`, `/api/forex-frameworks/{symbol}/{framework_id}/latest`, `/api/forex-frameworks/{symbol}/{framework_id}/history`, `/api/forex-frameworks/{symbol}/comparison`, `/api/forex-frameworks/{symbol}/thesis` provide read-only framework signals, confluence and analytical theses.
- Forex strategies/signals: `/api/forex-strategies`, `/api/forex-signals/*`, and `/api/forex-execution/*` provide deterministic paper-only forex candidate generation, manual confirmation, existing OMS integration, simulator fills, and active trade views. XAU/USD execution remains blocked until a compatible contract exists.
- IBKR paper acceptance: `/api/brokers/ibkr/status`, `/api/brokers/ibkr/account`, `/api/brokers/ibkr/contracts`, `/api/brokers/ibkr/connect`, `/api/brokers/ibkr/disconnect`, and `/api/brokers/ibkr/reconcile` expose paper-account verification, contract status, recovery and reconciliation state without returning secrets.
- Data quality/instruments: `/api/data-quality/*`, `/api/admin/data-quality/*`, `/api/instruments/*`
- Saved views/plugins/export: `/api/saved-views/*`, `/api/plugins/*`, `/api/export/*`

## WebSockets

- `/api/ws/quotes`: quote/tick subscriptions.
- `/api/ws/alerts`: alert push channel.
- `/api/ws/us-quotes`: US trades/bars stream.
- `/api/ws/depth`: depth snapshots.

## Auth Behavior

Most `/api/*` routes are protected by `AuthMiddleware` unless exempted. Exemptions include health/docs, `/api/auth`, `/api/v1`, and `/api/public`.

## Confirmed API Smoke Results

- `GET /health`: 200.
- `GET /healthz`: 200.
- `POST /api/auth/login`: 200 with generated admin credentials.
- `GET /api/watchlists` with bearer token: 200.

## Issues

- Redis-backed quote bus and L2 cache are not active in the current Docker run because `.env` sets `REDIS_URL=redis://localhost:6379/0`.
- Some route groups overlap legacy and newer APIs, especially portfolio, watchlists, backtests, chart routes, and screener routes.
- Several endpoints depend on external live data and may time out or return fallback data without provider keys.
# Phase 5 Market Structure API

- `GET /api/market-structure/configurations`
- `POST /api/market-structure/configurations/validate`
- `POST /api/market-structure/analyze`
- `GET /api/market-structure/snapshots/{snapshot_id}`

All endpoints are authenticated. Analysis accepts caller-supplied completed OHLCV bars and returns a paginated snapshot payload.
# Strategy API

Phase 6 adds `/api/strategies` for deterministic strategy registration, spec validation, evaluation, inspector summaries, and proposal lookup.

See `docs/STRATEGY_API.md` for endpoint details.

# Strategy Research API

Phase 7 adds `/api/research/strategy/*` for deterministic backtests, optimization, validation, scorecards, candidates, artifacts and complete workflow execution.

See `docs/RESEARCH_API.md`.

# Canonical Paper Trading API

Phase 8 adds `/api/trading/paper/*` for internal paper accounts, human-approved deployments, risk-gated intents, canonical paper orders, simulator fills, emergency disable and reconciliation.

See `docs/TRADING_API.md`.

# AI Research Assistant API

Phase 9 adds `POST /api/ai/research-brief` for deterministic, evidence-linked decision support.

The endpoint returns summary sections, evidence IDs, next research actions and explicit guardrails. It has no execution authority and does not submit orders, approve risk, mutate paper accounts or promote strategies.

See `docs/AI_RESEARCH_ASSISTANT.md`.

Phase 9B adds deterministic explanation endpoints:

- `POST /api/ai/explain/strategy`
- `POST /api/ai/explain/research`
- `POST /api/ai/explain/risk`
- `POST /api/ai/explain/order`
- `POST /api/ai/explain/position`
- `POST /api/ai/explain/reconciliation`

These endpoints use the canonical AI Assistant package and return read-only, evidence-linked explanations without LLM calls.

See `docs/AI_ARCHITECTURE.md`, `docs/AI_TOOL_REGISTRY.md`, `docs/AI_EVIDENCE.md`, and `docs/AI_EXPLAINABILITY.md`.

Research Agent endpoints are documented in `docs/RESEARCH_AGENT_API.md`.

Phase 9C adds deterministic evidence endpoints:

- `GET /api/ai/evidence/{bundle_id}`
- `POST /api/ai/evidence/retrieve`
- `POST /api/ai/evidence/lineage`

See `docs/AI_API.md`.
## AI Evidence Security

AI endpoints are authenticated, rate-limited, owner-scoped and return safe error codes with correlation IDs.
## Phase 11 Broker APIs

Phase 11 adds paper-only broker APIs:

- `GET /api/brokers`
- `GET /api/brokers/{broker}/health`
- `GET /api/brokers/{broker}/capabilities`
- `POST /api/brokers/{broker}/connect`
- `POST /api/brokers/{broker}/disconnect`
- `POST /api/brokers/{broker}/reconnect`
- `POST /api/brokers/ibkr/contracts/resolve`
- `GET /api/brokers/ibkr/quotes/{instrument_id}`
- `POST /api/brokers/ibkr/historical-bars`
- `GET /api/brokers/ibkr/accounts`
- `GET /api/brokers/ibkr/accounts/{account_id}/snapshot`
- `POST /api/brokers/ibkr/accounts/{account_id}/reconcile`

Broker order submission is exposed only through canonical paper OMS routes under `/api/trading/paper/orders/{order_id}`.
# Phase 12 Portfolio Operations

Phase 12 adds paper-only portfolio operations APIs under `/api/portfolio`, `/api/performance`, `/api/risk`, `/api/execution-analytics`, `/api/operations`, `/api/reports`, and `/api/replay`. See `docs/PHASE12_API.md`.
# Phase 12.1 API Note

Phase 12.1 adds canonical `/api/portfolios` workflow endpoints plus durable report schedule, replay session, and journal endpoints.
# FX-5B IBKR APIs

Added guarded endpoints:

- `POST /api/brokers/ibkr/read-only-acceptance`
- `GET /api/brokers/ibkr/acceptance/status`
- `POST /api/brokers/ibkr/acceptance/prepare`
- `POST /api/brokers/ibkr/acceptance/execute`
- `POST /api/brokers/ibkr/acceptance/exit`
- `GET /api/brokers/ibkr/acceptance/result`
- `GET /api/forex-execution/orders/{order_id}/events`
- `GET /api/forex-execution/orders/{order_id}/executions`
- `GET /api/forex-execution/incidents`

Execution remains blocked until real paper prerequisites pass.

# Architecture

This document describes the current Bensim Trading codebase as audited for Bensim Trading Phase 0. It is not a future-state design.

## Stack

- Frontend: React 18, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query, Zustand, lightweight-charts, Recharts, Nivo, Three.js, Playwright, Vitest.
- Backend: FastAPI, Python 3.11 in Docker, SQLAlchemy, Alembic, SQLite by default, optional PostgreSQL profile, Redis, asyncio background services.
- Packaging/runtime: Docker multi-stage build with Node builder and Python runtime. `docker-compose.yml` runs `backend`, `redis`, and optional `postgres`.
- Tests: Vitest unit/component tests, Playwright E2E tests, pytest backend tests.

## High-Level Shape

The backend is a single FastAPI app in `backend/main.py`. It mounts a large router tree from `backend/api/router.py`, starts background services in lifespan, exposes `/health`, `/livez`, `/readyz`, `/healthz`, `/metrics-lite`, and serves the built frontend SPA from `frontend/dist`.

The frontend is a Vite SPA. `frontend/src/App.tsx` defines top-level route groups for:

- `/equity`
- `/fno`
- `/backtesting`
- `/account`
- compatibility redirects such as `/portfolio` to `/equity/portfolio`

## Runtime Flow

1. Docker builds frontend assets with `npm run build`.
2. Docker installs Python dependencies from `backend/requirements.txt`.
3. Runtime starts `backend/entrypoint.sh`.
4. Alembic migrations run and admin seed runs.
5. FastAPI lifespan initializes DB, unified fetcher, cache, market data hub, instruments loader, news ingestor, PCR snapshot service, scanner alert scheduler, alert evaluator, and paper engine.
6. API and static SPA are served from the backend container on port `8000`.

## Data Flow

- Frontend uses `frontend/src/api/base.ts` as the shared Axios client with bearer-token injection and refresh retry.
- Many pages use TanStack Query for server state.
- Zustand stores persist UI state such as settings, shortcuts, navigation, chart workstation, notifications, and workspace templates.
- Backend route handlers call services in `backend/services`, domain modules under `backend/core`, `backend/fno`, `backend/risk_engine`, `backend/model_lab`, `backend/portfolio_lab`, etc.
- Market data flows through `backend.core.unified_fetcher`, provider clients, `backend.services.marketdata_hub`, Redis quote bus, and WebSocket routes.

## Authentication

- Auth routes live under `/api/auth` in `backend/equity/routes/auth.py`.
- Access tokens are JWT HS256 with 15 minute TTL.
- Refresh tokens are JWTs backed by `refresh_tokens` table rows and are revoked on use.
- `AuthMiddleware` protects `/api/*` except explicit exempt paths.
- Frontend stores access and refresh tokens in `localStorage`.
- E2E tests use `E2E_DEV_AUTH=1` and unsigned dev tokens.

## WebSockets

WebSocket endpoints are implemented in `backend/api/routes/stream.py`:

- `/api/ws/quotes`
- `/api/ws/alerts`
- `/api/ws/us-quotes`
- `/api/ws/depth`

`MarketDataHub` handles subscriptions, polling fallback, Kite stream, Finnhub stream, candle aggregation, alert listeners, and Redis pub/sub.

## Background Services

Started by app lifespan:

- Prefetch worker, gated by `BENSIM_PREFETCH_ENABLED` with legacy `OPENTERMINALUI_PREFETCH_ENABLED` compatibility.
- Instrument loader.
- News ingestor.
- PCR snapshot service.
- Scanner alert scheduler.
- Market data hub.
- Alert evaluator service.
- Paper trading engine.

## Docker

`docker-compose.yml` defines:

- `backend`: built from local `Dockerfile`, maps `${APP_PORT:-8000}:8000`.
- `redis`: `redis:7-alpine`, maps `6379`.
- `postgres`: `postgres:16-alpine`, profile `postgres`.

Docker uses `DOCKER_REDIS_URL=redis://redis:6379/0` by default for the backend service. Fresh deployments may set `COMPOSE_PROJECT_NAME=bensim`; existing persistent volumes retain legacy names for safety.

## Phase 3 Standards

Core hardening standards are documented in:

- `docs/CONFIGURATION.md`
- `docs/API_STANDARDS.md`
- `docs/MARKET_DATA_CONTRACT.md`
- `docs/SERVICE_INTERFACES.md`
- `docs/EVENTS.md`
- `docs/WEBSOCKETS.md`
- `docs/BACKGROUND_JOBS.md`
- `docs/REDIS.md`
- `docs/OBSERVABILITY.md`
- `docs/HEALTH_CHECKS.md`
# Phase 4 Market Data Foundation

The market-data layer now includes a canonical foundation package at `backend/market_data`. Existing providers remain in compatibility mode while new code can use registry, routing, canonical models, provenance, validation, cache policy, calendar, resampling, resilience and snapshot primitives.
# Phase 5 Market Structure Extension

Bensim now includes a deterministic market-structure package at `backend/market_structure`. It consumes validated OHLCV bars and produces reproducible swings, structure breaks, SMC objects, overlays, events, explanations and feature rows without provider, Redis or database dependencies.
# Phase 6 Strategy Framework

The deterministic strategy framework lives in `backend/strategies` and is exposed through `/api/strategies`.

It consumes completed OHLCV bars, indicator features, and Phase 5 market-structure features, then returns decisions, evidence, events, and proposal-only trade intents. It does not submit orders or change broker/execution behavior.

See `docs/STRATEGY_ENGINE.md` and `docs/STRATEGY_SPECIFICATION.md`.

# Phase 7 Research Workflow

The canonical strategy research workflow lives in `backend/research` and is exposed under `/api/research/strategy`.

It connects Phase 6 strategy decisions to deterministic event-driven backtests, optimization, walk-forward validation, robustness, scorecards and research-only candidates.

# Phase 8 Canonical Paper Trading

The controlled paper-trading boundary lives in `backend/trading` and is exposed under `/api/trading/paper`.

It separates deployment approval, pre-trade risk, OMS lifecycle, internal simulated execution, portfolio ledger accounting, emergency controls and reconciliation. Legacy `/api/paper/*` and `/api/oms/*` routes remain available for compatibility.

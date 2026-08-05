# Modules

This is the current module inventory observed during Phase 0.

## Frontend

- App shell and routing: `frontend/src/App.tsx`, protected routes, lazy route loading, terminal shell overlays.
- API layer: 51 files in `frontend/src/api`, mostly thin wrappers over Axios/fetch.
- State: Zustand stores in `frontend/src/store` for alerts, charts, navigation, notifications, screener, settings, shortcuts, stocks, and workspace templates.
- Layout: `frontend/src/components/layout`, terminal UI components, command bar, launchpad, ticker tape, sidebars, mobile nav.
- Charting: `frontend/src/components/chart`, `frontend/src/shared/chart`, chart workstation components, custom indicators, drawing engine, templates, real-time aggregation.
- Equity: stock detail, security hub, screener, dashboard, portfolio, watchlist, news, alerts, risk, OMS/Ops, research, heatmap, fixed income, ETF, crypto, commodities, forex, pair trading, stat lab.
- FNO: option chain, greeks, futures, OI, PCR, strategy, flow, heatmap, expiry.
- Backtesting: backtesting page, model lab, governance, algorithm framework lab, portfolio optimizer.
- Account/auth: login/register/forgot access, account preferences, profile/local settings.
- Agent UI: frontend agent console and launcher are present.

## Backend

- App and router: `backend/main.py`, `backend/api/router.py`.
- Auth: `backend/auth`, `backend/equity/routes/auth.py`.
- Models/database: `backend/models`, `backend/shared/db.py`, `backend/db/base.py`, Alembic versions.
- Market data: `backend/core/unified_fetcher.py`, Yahoo/FMP/Finnhub/Kite clients, provider registry, market data hub, Redis quote bus.
- Equity routes: stocks, quotes, chart, fundamentals, news, alerts, portfolio, watchlists, screeners, events, earnings, mutual funds, peers, paper, plugins.
- FNO: services and routes for option chain, PCR, OI, greeks, strategy builder, futures, flow.
- Portfolio: legacy portfolio routes plus newer `portfolios` and `portfolio_lab` modules.
- Risk/OMS/Ops: risk engine, stress tests, factor attribution, OMS routes, ops kill-switch/data quality routes.
- Backtesting/model lab: strategy runner, backtest jobs, model lab, robustness, portfolio backtests.
- Research/AI: research ingest/search, research autopilot, AI/agent routes, LLM provider wrappers.
- Data quality/instruments: data quality dashboards/admin routes, instrument master/search.
- Plugins: plugin loader and example plugins.

## Feature Status

Confirmed working by automated tests or smoke checks:

- Docker backend and Redis containers start.
- Backend `/health`, `/healthz`, login, authenticated watchlists.
- Frontend production build.
- Frontend unit/component tests.
- Many Playwright flows: auth smoke, alerts, chart workstation, backtesting tabs, correlation, DOM ladder, factor attribution, FNO option chain, hotkey trading, insider activity, market heatmap, model lab, multi-timeframe, notification center, options flow, portfolio lab, position sizing, risk/OMS/Ops, screener scanner, screenshots, stat lab tabs, stress test, terminal GO bar, time and sales, trade journal, workspace templates.
- Isolated backend pytest for auth, pure jump volatility, and WebSocket quotes.

Incomplete, prototype, mock, or fragile areas:

- Full backend pytest suite is blocked by import-path collisions around top-level `models` vs `backend/models`, plus an `nlp` import circularity.
- Playwright custom formula screener cannot find the expected "Custom Formula" control.
- Playwright pair trading live-data verdict does not render within timeout.
- Playwright StatLab CAPM live-data beta result does not render within timeout.
- Docker Redis integration is misconfigured by `.env` and falls back to in-memory.
- Several data services explicitly return mock/fallback data when keys or upstream data are absent, including fixed income, economics, insider monitor, sector rotation, bond service, and some test/demo surfaces.
- Top-level legacy modules (`core`, `models`, `nlp`, `db`, `ui`, `trade_screens`) coexist with `backend/*` modules and can collide during tests.

## Future Bensim Placement Recommendations

- Research Lab: extend `backend/core/research`, `backend/api/routes/research.py`, `backend/api/routes/research_autopilot.py`, and frontend `ResearchPage`/`ResearchAutopilot`.
- Optimization Lab: extend `backend/api/routes/portfolio_optimizer.py`, `backend/core/riskfolio`, and frontend `PortfolioOptimizer`.
- Validation Lab: extend `backend/model_lab`, `backend/core/backtest_robustness.py`, `backend/api/routes/model_lab_robustness.py`, and Backtesting UI.
- Strategy Engine: consolidate around `backend/core/strategy_runner.py`, `backend/core/framework`, and `backend/strategy_export`.
- Risk Engine: extend `backend/risk_engine` and `backend/api/routes/risk.py`.
- Broker Layer: add behind existing adapter/service boundaries, likely separate from current Kite-specific routes.
- AI Analysis: extend existing `backend/api/routes/ai.py`, `backend/api/routes/agent.py`, `backend/services/llm`, and frontend `agent`.
- Trading Engine: build on paper trading, OMS, execution simulation, and risk guardrails before adding broker execution.
# Phase 5 Market Structure Module

`backend/market_structure` contains pure calculation modules for configuration, swings, trend, BOS/CHoCH/MSS, displacement, liquidity, FVGs, order blocks, dealing ranges, sessions, multi-timeframe relationships, scoring, explanations, events and overlay serialization.
# Strategy Framework

- Backend package: `backend/strategies`
- API route: `backend/api/routes/strategies.py`
- Frontend client: `frontend/src/api/strategies.ts`
- Frontend inspector: `frontend/src/components/chart-workstation/StrategyDecisionPanel.tsx`

The module is deterministic and proposal-only. Reference strategies are listed in `docs/REFERENCE_STRATEGIES.md`.

# Strategy Research

- Backend package: `backend/research`
- API route: `backend/api/routes/strategy_research.py`
- Frontend page: `frontend/src/pages/ResearchWorkflowPage.tsx`

This module is research-only and does not activate paper or live trading.

# Canonical Paper Trading

- Backend package: `backend/trading`
- API route: `backend/api/routes/trading.py`
- Frontend panel: `frontend/src/components/trading/CanonicalPaperControls.tsx`

This module owns Phase 8 internal paper accounts, strategy deployments, risk policies/evaluations, OMS state transitions, simulated fills, portfolio ledger entries, emergency controls, snapshots and reconciliation. It does not connect to brokers.

# Technical Debt

Prioritized findings from Phase 0.

## High Impact

1. Docker Redis misconfiguration: current `.env` uses `REDIS_URL=redis://localhost:6379/0`, causing Redis failures inside Docker and fallback to in-memory behavior.
2. Backend full pytest collection is blocked by package-name collisions. `models.pure_jump_vol` can resolve incorrectly when pytest collects all tests; `nlp.sentiment` also has a circular import failure under full collection.
3. E2E failures remain in custom formula screener, pair trading live-data verdict, and StatLab CAPM live-data result.
4. Dependency audit reports 14 npm vulnerabilities: 1 low, 6 moderate, 6 high, 1 critical. Direct affected packages include axios, postcss, react-mosaic-component, react-router-dom, vite, and vitest.

## Medium Impact

5. Large frontend components are difficult to maintain: `TradingChart.tsx` ~3083 lines, `ChartWorkstationPage.tsx` ~2519 lines, `Backtesting.tsx` ~1760 lines, `Portfolio.tsx` ~1364 lines.
6. Large backend services/routes concentrate behavior: `portfolio_analytics.py`, `chart.py`, `us_tick_stream.py`, `marketdata_hub.py`, `news.py`, `strategy_runner.py`, `commodity_service.py`.
7. Multiple overlapping API families exist for portfolios, watchlists, charts, screeners, and backtests.
8. Several modules explicitly return mock/fallback data when external keys are missing. Useful for demos, but production readiness varies by feature.
9. Frontend stores auth tokens in `localStorage`, increasing exposure if XSS appears.
10. Test warnings include React `act(...)` warnings, React Router v7 future warnings, jsdom canvas-not-implemented errors, and chart container size warnings.

## Lower Impact

11. `.env.example` and docs contain some mojibake characters from encoding conversion.
12. Top-level legacy folders (`core`, `models`, `nlp`, `db`, `ui`, `trade_screens`) duplicate or overlap backend packages.
13. `npm ci` uses package-lock versions newer than package ranges in some places; this is expected but should be reviewed before upgrades.
14. `Dockerfile` uses `npm install` rather than `npm ci`, reducing build reproducibility.

## Counts

- Source files scanned: 1254.
- Source lines scanned: 184084.
- TODO markers: 17 matches in 7 files.
- FIXME markers: 0.
- NotImplementedError: 12 matches in 2 files.
- Placeholder matches: 198 in 87 files.
- Mock-data matches: 9 in 7 files.


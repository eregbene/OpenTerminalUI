# Module Boundaries

## Domains

- Authentication and users: `backend/auth`, account routes, JWT middleware.
- Configuration: `backend/config`.
- Market data and reference data: `backend/core/unified_fetcher.py`, `backend/adapters`, `backend/providers`, `backend/instruments`, `backend/services/marketdata_hub.py`.
- Equities: `backend/equity`, equity routes under `backend/api/routes`.
- Forex, crypto, commodities, fixed income, FNO: domain route folders and service modules.
- Screeners and research: `backend/core/screener.py`, `backend/screener`, `backend/core/research`.
- Portfolio, risk, OMS, paper trading: `backend/portfolio_*`, `backend/risk_engine`, `backend/oms`, `backend/paper_trading`.
- Backtesting and statistical research: `backend/core/backtester.py`, `backend/core/statlab`, model and portfolio labs.
- AI and agents: `backend/agent`, `backend/services/ai_service.py`, LLM provider modules.
- News and intelligence: `backend/bg_services/news_ingestor.py`, news routes, NLP services.
- Alerts and notifications: `backend/alerts`, notification routes.
- WebSockets and background jobs: `backend/services/marketdata_hub.py`, `backend/bg_services`, FastAPI `BackgroundTasks`.

## Public Interfaces

Phase 3 introduces shared protocols in `backend/core/contracts/services.py`. Existing services are not fully migrated yet; new work should depend on capability-specific protocols rather than concrete provider classes.

## Known Boundary Issues

- `backend/core/unified_fetcher.py` still coordinates many provider-specific clients directly.
- Several route modules directly raise `HTTPException` and directly access database/session helpers.
- Some model lab, portfolio lab, and backtest services mix orchestration, persistence, and computation.
- WebSocket message schemas are mostly convention-based; the new envelope helpers document the future path.


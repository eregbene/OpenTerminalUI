# Paper Trading System Inventory

Phase 8 found multiple execution-related paths.

| Area | Path | Classification | Notes |
|---|---|---|---|
| Legacy virtual paper trading | `backend/paper_trading/service.py`, `backend/api/routes/paper.py` | active, paper-only, persistence-backed, unsafe for production use | Mutates virtual portfolio state directly and can fill market orders from current marks. Preserved for compatibility. |
| Legacy OMS/compliance | `backend/oms/service.py`, `backend/oms/routes.py` | active, prototype, persistence-backed | Has audit, restricted list and kill switch checks, but not a full lifecycle or ledger. |
| Legacy risk analytics | `backend/risk_engine/*` | active, analytics-only | Portfolio VaR, stress, attribution; not a canonical pre-trade gate. |
| Portfolio pages/routes | `backend/api/routes/portfolio.py`, `frontend/src/pages/Portfolio.tsx` | active, portfolio-management | Not paper-account ledger ownership. |
| Backtest execution | `backend/research/backtests.py`, `backend/core/backtester.py` | backtest-only | Uses simulation assumptions for research; does not own paper balances. |
| Strategy engine | `backend/strategies/*` | active, proposal producer | Produces decisions/proposals and must not place orders directly. |
| Canonical paper controls | `backend/trading/*`, `backend/api/routes/trading.py` | active Phase 8, paper-only, process/file persisted | Deterministic approval, risk, OMS, simulator, ledger and reconciliation path. |

No external broker integration was added.

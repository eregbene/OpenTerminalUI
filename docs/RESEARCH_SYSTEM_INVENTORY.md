# Research System Inventory

Existing modules inspected:

- `backend/core/backtester.py`: active legacy/simple backtester, provider-coupled, suitable for compatibility.
- `backend/core/single_asset_backtest.py`: active single-asset engine, deterministic for tests, legacy-compatible.
- `backend/core/vectorized_backtest.py`: active vectorized sweep, strategy-specific, suitable as legacy baseline.
- `backend/core/framework/engine.py`: active modular algorithm framework, larger architecture, adapted through contracts later.
- `backend/services/backtest_jobs.py`: active async job orchestration used by Model Lab.
- `backend/model_lab`: active experiment/reporting workflow, persistence-backed, not replaced.
- `backend/portfolio_backtests`: active portfolio backtest workflow, persistence-backed, not replaced.
- `backend/core/param_optimizer.py`: legacy parameter optimization utilities.
- `backend/core/walk_forward.py`: active validation helper, usable as reference.
- `backend/core/backtest_robustness.py`: active robustness helper, usable as reference.
- `backend/core/statlab`: active statistical modules, dependency-heavy and not strategy-specific.
- `backend/core/riskfolio`: active portfolio optimization, dependency-heavy.
- `backend/api/routes/pair_trading.py`: active statistical pair research, provider/statistics coupled.
- `frontend/src/pages/Backtesting.tsx`: active legacy Backtesting Console.
- `frontend/src/pages/ModelLab*.tsx`: active Model Lab UI.
- `frontend/src/pages/PortfolioLab*.tsx`: active Portfolio Lab UI.

Phase 7 adds `backend/research` as the canonical deterministic strategy-research workflow and preserves all legacy paths.

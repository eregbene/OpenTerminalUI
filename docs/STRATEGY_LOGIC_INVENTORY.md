# Strategy Logic Inventory

Phase 6 adds a deterministic strategy framework under `backend/strategies`.

Existing legacy strategy paths remain:

- `backend/core/strategy_runner.py`: legacy script/example runner. It includes deterministic examples and constrained inline strategy execution. It is not the Phase 6 rule engine.
- `backend/core/framework/engine.py`: algorithm framework/backtest pipeline. Phase 6 exposes an adapter contract so future research/backtest work can consume decisions without changing this engine.
- `backend/core/formula_engine.py`: screener formula evaluator. It remains scoped to screening formulas.
- `frontend/src/pages/AlgorithmFrameworkLab.tsx`: existing algorithm lab UI. It was not redesigned.

Phase 6 canonical strategy logic:

- Strategy specs: `backend/strategies/models.py`
- Registry: `backend/strategies/registry.py`
- Rule validation/evaluation: `backend/strategies/evaluator.py`
- Indicator and SMC input construction: `backend/strategies/indicators.py`, `backend/strategies/inputs.py`
- Proposal/event generation: `backend/strategies/proposals.py`, `backend/strategies/events.py`
- API: `backend/api/routes/strategies.py`

# Research Architecture

Phase 7 canonical workflow:

Dataset snapshot -> strategy spec -> event-driven backtest -> metrics -> optimization -> walk-forward validation -> robustness -> scorecard -> research candidate.

Implementation:

- Models: `backend/research/models.py`
- Backtest simulation: `backend/research/backtests.py`
- Metrics: `backend/research/metrics.py`
- Optimization: `backend/research/optimization.py`
- Validation/robustness: `backend/research/validation.py`
- Scorecards/candidates: `backend/research/scorecards.py`
- Artifacts/lineage/events: `backend/research/artifacts.py`, `lineage.py`, `events.py`
- Service/API: `backend/research/services.py`, `backend/api/routes/strategy_research.py`

Durability:

- File-backed JSON registry and artifact store are implemented for the vertical slice.
- Alembic revision `0012_strategy_research` defines relational tables for durable migration.

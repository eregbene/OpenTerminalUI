# Strategy Specification

Strategies are Pydantic `StrategySpec` objects.

Main sections:

- `strategy`: id, name, version, family, status, description.
- `universe`: supported asset classes and optional symbols.
- `timeframes`: execution timeframe plus optional context timeframes.
- `data_policy`: delayed, cached, fallback, simulated, freshness, and quality-score controls.
- `entry`: long and short rule trees.
- `exit`: reserved deterministic exit rule trees.
- `invalidation`: proposal invalidation reference.
- `targets`: proposal target levels.
- `sizing_intent`: sizing metadata only; no order sizing is executed.
- `cooldown`: bar-based proposal throttling.
- `required_features` and `required_history`: validation and lookback requirements.

Specs expose a stable hash used in decisions, evaluations, state, and event idempotency.

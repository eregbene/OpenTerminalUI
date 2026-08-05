# Strategy Backtest Adapter

`backend/strategies/adapters/backtest.py` provides `decisions_to_backtest_signals`.

The adapter converts `StrategyEvaluation` decisions to simple signal dictionaries:

- timestamp
- symbol
- strategy id
- decision type
- direction
- quality score
- optional proposal payload

This preserves compatibility with future research/backtest work without changing existing backtest engines in Phase 6.

Phase 7 adds `backend/research/backtests.py` as the canonical event-driven consumer of Phase 6 proposals.

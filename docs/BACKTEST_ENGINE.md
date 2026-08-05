# Backtest Engine

The canonical Phase 7 engine is `EventDrivenBacktester`.

Sequence:

1. A bar closes.
2. Strategy features become available.
3. Phase 6 strategy evaluates.
4. A proposal becomes a simulated order.
5. The order is eligible on a future bar.
6. Future bars determine fills, stops and targets.

The strategy engine makes analytical decisions. The backtester owns simulation, costs, accounting and metrics.

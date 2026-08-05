# Strategy Engine

The Phase 6 strategy engine is deterministic and proposal-only.

Pipeline:

1. Completed OHLCV bars are normalized.
2. Indicator features are computed from completed bars only.
3. Phase 5 market-structure features are merged into the feature namespace.
4. Strategy specs are validated against allowed feature namespaces and operators.
5. Entry rules are evaluated point-in-time.
6. A decision is created with condition evidence.
7. If long/short rules pass without conflict, a trade proposal is created.
8. Events are emitted for decisions and proposals.

The engine does not submit orders, simulate fills, select strategies with ML, or mutate rules at runtime.

Phase 7 consumes strategy decisions and proposals through the research backtester. Fill logic remains outside the strategy engine.

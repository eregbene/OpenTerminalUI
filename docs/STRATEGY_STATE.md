# Strategy State

`StrategyState` tracks deterministic strategy lifecycle metadata:

- strategy id/hash
- instrument id
- current state
- last decision time
- last proposal bar index
- active proposal ids
- consumed event ids
- transitions

Cooldown checks use `last_proposal_bar_index`. State is included in evaluations but is not persisted to a database in Phase 6.

# Strategy Decisions

Each `StrategyDecision` records:

- strategy id/version/hash
- instrument and timeframe
- as-of timestamp
- decision type
- direction
- full rule evidence
- input references
- data-quality metadata
- optional proposal

Decision types:

- `long`
- `short`
- `exit_long`
- `exit_short`
- `hold`
- `no_action`
- `blocked`
- `insufficient_data`

Phase 6 currently creates entry decisions only. Exit decisions are modeled for future deterministic lifecycle work.

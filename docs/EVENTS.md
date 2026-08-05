# Events

`backend/core/contracts/events.py` defines `EventEnvelope`.

Required fields:

- `event_id`
- `event_type`
- `version`
- `timestamp`
- `source`
- `correlation_id`
- `idempotency_key`
- `payload`

Internal events and frontend WebSocket messages remain distinct. New WebSocket work can use `backend/core/contracts/websocket.py` for an envelope-compatible payload while preserving the legacy `type` field.
# Phase 5 SMC Events

Market-structure analysis emits versioned in-memory events such as `market_structure.swing.confirmed`, `market_structure.bos.confirmed`, `market_structure.choch.confirmed`, `market_structure.mss.confirmed`, `smc.liquidity.swept`, `smc.fvg.created`, `smc.fvg.mitigated`, `smc.order_block.confirmed`, `smc.order_block.invalidated` and `smc.dealing_range.updated`.
# Strategy Events

Phase 6 defines in-memory strategy events for deterministic decisions and trade proposals:

- `strategy.decision.created`
- `strategy.proposal.created`

See `docs/STRATEGY_EVENTS.md`.

# Research Events

Phase 7 emits research lifecycle event records such as `research.experiment.created`, `research.backtest.completed`, `research.optimization.completed`, `research.validation.completed`, `research.scorecard.generated`, `research.candidate.qualified` and `research.candidate.rejected`.

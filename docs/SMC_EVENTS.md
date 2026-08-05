# SMC Events

The engine emits Phase 3-compatible event records in-memory.

Implemented event types:

- `market_structure.swing.confirmed`
- `market_structure.bos.confirmed`
- `market_structure.choch.confirmed`
- `market_structure.mss.confirmed`
- `market_structure.break.confirmed`
- `smc.liquidity.swept`
- `smc.fvg.created`
- `smc.fvg.mitigated`
- `smc.order_block.confirmed`
- `smc.order_block.invalidated`
- `smc.dealing_range.updated`

Events include schema version, correlation ID, idempotency key, source dataset reference and configuration hash.

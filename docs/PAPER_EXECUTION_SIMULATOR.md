# Paper Execution Simulator

The Phase 8 simulator is internal only.

Implemented:

- market orders fill on a future submitted/acknowledged event
- limit orders require conservative eligible prices
- stop and stop-limit activation are distinct from fill price
- slippage and spread bps are applied adversely
- optional liquidity cap creates deterministic partial fills
- every fill records execution model id and version

Unsupported combinations are rejected before simulator submission where validation can detect them.

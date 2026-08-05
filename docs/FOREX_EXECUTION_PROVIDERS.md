# Forex Execution Providers

FX-5 separates execution providers:

- `LOCAL_SIMULATOR`
- `IBKR_PAPER`
- `DISABLED`

When `IBKR_PAPER` is selected and IBKR is unavailable or unverified, submission is rejected. The system does not silently fall back to the local simulator.

Local simulator fills are development/test fills. IBKR paper fills must come from the broker adapter and must not be overwritten by external analysis prices.
# FX-5B Update

Execution providers are:

- `LOCAL_SIMULATOR`
- `FIXTURE_IBKR`
- `REAL_IBKR_PAPER`

Fixture IBKR is not real connectivity and cannot execute real paper orders.

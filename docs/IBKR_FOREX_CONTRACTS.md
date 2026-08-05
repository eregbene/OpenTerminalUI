# IBKR Forex Contracts

FX-5 verifies canonical FX contracts through the existing IBKR adapter.

## Supported For Acceptance

- `EURUSD`
- `GBPUSD`
- `USDJPY`

Each record stores canonical symbol, base/quote, security type, exchange, local symbol, `con_id`, minimum tick, quantity rules, accepted order types, verification time, and content hash.

## Blocked

`XAUUSD` remains `CONTRACT_UNAVAILABLE`. It must not be submitted while the analysis source is a gold futures proxy.
# FX-5B Update

Fixture con_ids are explicitly marked `verification_source=FIXTURE` and `usable_for_real_submission=false`.

Real submission requires `verification_source=REAL_IBKR_PAPER`. EUR/USD is the only acceptance execution symbol; GBP/USD and USD/JPY may be resolved read-only. XAU/USD remains blocked.

# IBKR Real Adapter

FX-5B defines three execution adapter states:

- `LOCAL_SIMULATOR`: local deterministic paper simulation.
- `FIXTURE_IBKR`: deterministic broker fixture for tests and UI development.
- `REAL_IBKR_PAPER`: intended TWS or IB Gateway paper connectivity.

`FIXTURE_IBKR` must never be displayed or treated as a real broker connection. In `IBKR_MODE=PAPER`, the backend rejects the simulated adapter and requires PostgreSQL persistence.

The repository currently has no approved real IBKR client library wired for live TWS/Gateway I/O. Until that adapter is completed and verified, real order submission remains blocked.

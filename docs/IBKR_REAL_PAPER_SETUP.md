# IBKR Real Paper Setup

Required environment:

```text
IBKR_ENABLED=1
IBKR_MODE=PAPER
IBKR_HOST=host.docker.internal
IBKR_PORT=7497
IBKR_CLIENT_ID=111
IBKR_EXPECTED_ACCOUNT=<paper account>
IBKR_ACCOUNT_ALLOWLIST=<paper account>
IBKR_MARKET_DATA_TYPE=DELAYED
```

`IBKR_MODE=LIVE` is unsupported and must not be used.

Before any order:

1. Start TWS or IB Gateway in paper mode.
2. Enable API access.
3. Verify the expected account is allowlisted.
4. Run read-only acceptance.
5. Resolve EUR/USD from the broker session.
6. Confirm reconciliation is matched.

# IBKR Contract Resolution

Supported initial scope:

- stocks,
- ETFs,
- forex cash pairs,
- data-only indices.

Ambiguous contracts are rejected. Forex pairs use explicit base/quote semantics and IDEALPRO routing. Contract metadata includes conId, exchange, currency, security type, timestamp, and resolution version.

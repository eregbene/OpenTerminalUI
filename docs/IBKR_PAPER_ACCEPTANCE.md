# IBKR Paper Acceptance

FX-5 adds an IBKR paper acceptance layer around the existing broker abstraction.

## Scope

- Paper only.
- Uses the existing `backend.brokers` registry and IBKR adapter.
- No second IBKR client is introduced.
- Real trade execution is blocked unless the paper account is conclusively verified.

## Current Acceptance Status

The code supports deterministic mocked acceptance through the existing simulated IBKR adapter. Real TWS/Gateway acceptance still requires an operator-run connection to a verified paper account.

## Required Real Gate

Before any real IBKR paper trade:

- account mode must be `PAPER`
- trading environment must be `PAPER`
- live execution must be false
- account must be allow-listed
- contract must be verified
- reconciliation must be nonblocking
- recovery must not be in progress
- XAU/USD must remain blocked
# FX-5B Update

Critical broker acceptance state now has a PostgreSQL schema. Legacy JSON state remains fixture-only and cannot be used for real IBKR paper submission.

Real paper submission is blocked unless `IBKR_MODE=PAPER`, the adapter is non-fixture, the account is verified and allowlisted, EUR/USD is resolved from the broker session, and reconciliation is matched.

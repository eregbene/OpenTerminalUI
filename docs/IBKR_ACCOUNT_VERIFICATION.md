# IBKR Account Verification

IBKR paper acceptance records:

- broker
- masked account ID
- account alias
- account type
- account mode
- trading environment
- host
- port
- client ID
- session ID
- connection and verification timestamps
- verification source
- rejection reasons

Reject reasons include `UNKNOWN_ACCOUNT_MODE`, `LIVE_ACCOUNT`, `AMBIGUOUS_ACCOUNT`, `ACCOUNT_CHANGED`, and `ACCOUNT_ID_MISMATCH`.

Sensitive account identifiers are masked in API/UI responses.
# FX-5B Update

Account verification now rejects unknown, ambiguous, mismatched, non-allowlisted, and live accounts. Account identifiers must be masked in UI/log output and hashed for durable matching.

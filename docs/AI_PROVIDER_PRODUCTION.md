# AI Provider Production

Provider production controls live in `backend/ai_provider`.

Implemented controls:

- provider-reported token usage when available
- estimated fallback usage
- decimal-safe cost estimates
- pricing table versioning
- budget reservations and reconciliation
- concurrent request limits
- provider health status
- circuit breaker states: `CLOSED`, `OPEN`, `HALF_OPEN`, `DISABLED`
- deterministic fallback

Provider secrets are resolved through `backend/ai_secrets` and are never returned by status APIs.

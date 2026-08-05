# Provider Health

Implemented endpoints:

- `GET /api/system/providers`
- `GET /api/system/data-health`

Both are authenticated and redact secrets. Public `/health`, `/livez`, `/readyz` remain high-level and do not expose provider credentials.

Diagnostics include configured/authenticated/entitlement state, capabilities, asset classes, health state, latency placeholder, last error and circuit-breaker fields.

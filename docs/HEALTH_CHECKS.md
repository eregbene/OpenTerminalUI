# Health Checks

## Endpoints

- `/health`: lightweight compatibility endpoint.
- `/livez`: liveness; confirms process is serving.
- `/readyz`: readiness and dependency snapshot.
- `/healthz`: compatibility alias for readiness-style diagnostics.
- `/metrics-lite`: lightweight operational counters.

## Readiness Coverage

Readiness reports database, cache tiers, market-data hub status, unified fetcher initialization, and service registry state.

Detailed diagnostics must avoid exposing secrets or provider credentials.


# Observability

## Request Context

Phase 3 adds request and correlation IDs to every HTTP response:

- `X-Request-ID`
- `X-Correlation-ID`

## Logging

`backend/core/observability.py` supports optional JSON logs with:

```text
BENSIM_LOG_FORMAT=json
```

Fields include timestamp, severity, service, module, request ID, correlation ID, job ID, provider, symbol, asset class, error code, and duration.

Secrets, tokens, and API keys must not be logged.


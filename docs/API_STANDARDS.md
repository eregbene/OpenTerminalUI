# API Standards

## Success Payloads

Existing success payloads are preserved. Do not wrap stable endpoints merely for consistency.

## Errors

New endpoints should use `BensimAPIError` and the standard envelope:

```json
{
  "error": {
    "code": "MARKET_DATA_UNAVAILABLE",
    "message": "Market data is temporarily unavailable.",
    "details": {},
    "request_id": "..."
  }
}
```

Implemented in `backend/core/contracts/api.py`; registered in `backend/main.py` for custom Bensim errors.

## Request IDs

Every HTTP response gets:

- `X-Request-ID`
- `X-Correlation-ID`

Incoming headers are honored; otherwise the backend generates IDs.
# Phase 4 Extension

Provider diagnostics were added at authenticated `/api/system/providers` and `/api/system/data-health`. Error payloads and diagnostics must not expose provider secrets.

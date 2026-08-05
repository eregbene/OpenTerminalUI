# Market Data Contract

Phase 3 defines a shared vocabulary in `backend/core/contracts/market_data.py`.

## Freshness

- `real_time`
- `delayed`
- `end_of_day`
- `historical`
- `cached`
- `simulated`
- `fallback`
- `stale`
- `unavailable`

## Entitlement

- `available`
- `subscription_required`
- `authentication_failure`
- `rate_limited`
- `unknown`

## Provider Health

- `ok`
- `degraded`
- `down`
- `disabled`
- `unknown`

`MarketDataStatus.from_quote()` is the first compatibility adapter for quote-like payloads.
# Phase 4 Extension

Phase 4 adds canonical models under `backend/market_data/models.py`, capability contracts under `backend/market_data/capabilities.py`, and provenance/quality metadata that separates freshness, origin, entitlement, availability and provider health. Existing response contracts remain compatible unless explicitly migrated.

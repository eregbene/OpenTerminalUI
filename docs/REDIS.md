# Redis Architecture

## Current Usage

- Cache tier in `backend/shared/cache.py`.
- Market quote bus and pub/sub in market-data services.
- Aggregator lock for candle aggregation.

## Key Prefixes

New cache keys use `BENSIM_REDIS_KEY_PREFIX`, defaulting to `bensim`.

Legacy cache keys using `openterminalui` are still read as fallback when possible. Existing Redis keys are not flushed or renamed.

## Health

Cache health is exposed through `/readyz` and `/healthz`, including L1 memory, Redis, and SQLite cache tier status.
# Phase 4 Extension

Market-data cache policies are documented in `docs/CACHE_POLICY.md` and implemented in `backend/market_data/cache_policy.py`. Cached data must be marked cached/stale where applicable and must not be labelled realtime.

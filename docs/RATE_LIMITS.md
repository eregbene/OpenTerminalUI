# Rate Limits

Implemented representation: `RateLimitPolicy` in `backend/market_data/registry.py`.

Known limits are not guessed. Providers without documented configured limits are marked `source="unknown"` or `source="provider_plan"`.

Phase 4 adds policy fields for per-second, per-minute, per-day and concurrency limits. Queueing/coalescing is documented as future work except for existing provider-specific behavior.

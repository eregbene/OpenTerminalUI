# Provider Migration

Representative migration in Phase 4:

- Provider registry metadata for Yahoo, NSE, FMP, Finnhub, FRED and internal demo data.
- Legacy quote and OHLCV normalization helpers.
- Router and diagnostics use canonical registry.
- Frontend shows representative data-quality badges.

Legacy paths remaining:

- chart endpoints still call existing chart providers directly
- NSE/Kite/Yahoo adapter chain remains separate
- crypto and Binance streaming services remain legacy
- FNO option-chain routes remain legacy
- fundamentals/news/economic routes are registered but not fully normalized

Migration pattern: wrap a legacy provider with a capability adapter, normalize into canonical models, validate, route via `MarketDataRouter`, and add metadata non-destructively to existing payloads.

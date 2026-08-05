# Timeframes And Resampling

Implemented in `backend/market_data/resampling.py`.

Current support:

- `1m`, `5m`, `15m`, `30m`, `1h`, `1d`
- lower timeframe to higher timeframe aggregation
- completed bars only by default
- explicit incomplete-bar handling
- volume, trade count and VWAP aggregation
- transformation lineage on output bars

Remaining: full exchange-holiday/session-aware resampling across futures overnight sessions and complex DST cases.

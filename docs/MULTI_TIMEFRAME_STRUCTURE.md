# Multi-Timeframe Structure

The package includes `align_timeframes` for timestamp-safe higher/lower timeframe relationship checks.

Outputs:

- `aligned_bullish`
- `aligned_bearish`
- `conflicting`
- `neutral`
- `insufficient_data`

Higher-timeframe snapshots must be complete as-of the lower-timeframe event time. The first vertical slice provides the relationship contract; broad UI and screener use are deferred.

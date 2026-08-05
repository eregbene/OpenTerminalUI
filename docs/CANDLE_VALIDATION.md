# Candle Validation

Implemented in `backend/market_data/validation.py`.

Checks include:

- duplicate bars
- missing spacing
- out-of-order timestamps
- OHLC relationship errors
- negative prices
- negative/zero volume
- incomplete final bars
- timeframe mismatch
- stale bars
- timestamp drift
- outlier price moves
- possible corporate actions

Validation adds quality flags and score adjustments rather than rejecting every questionable bar.

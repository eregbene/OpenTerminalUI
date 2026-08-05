# XAU/USD Support

`XAUUSD` is supported as a canonical Bensim Trading instrument for charts, quotes, forex intelligence, and framework analysis.

## Data Source

The current source is Yahoo `GC=F`, a COMEX gold futures proxy. It is not silently treated as spot gold.

API responses expose:

- `data_source_type`: `gold_futures_proxy`
- `is_proxy`: `true`
- `proxy_for`: `XAUUSD`

## Instrument Metadata

- Symbol: `XAUUSD`
- Display name: `XAU/USD`
- Asset type: `commodity`
- Instrument class: `precious_metal`
- Base: `XAU`
- Quote: `USD`
- Pip size: `0.10`
- Point size: `1.00`
- Display precision: `2`

## Limitations

Spot-specific spreads, swaps, broker session details, and OTC liquidity are not inferred from the futures proxy. Provider replacement should happen inside `backend/forex_intelligence/instruments.py`, leaving the canonical `XAUUSD` symbol unchanged.

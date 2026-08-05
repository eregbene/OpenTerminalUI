# Forex and Metals Instruments

Bensim Trading currently treats forex as a first-class read-only asset class centered on the major currency pairs plus a metals proxy instrument:

- `EURUSD`
- `GBPUSD`
- `USDJPY`
- `USDCHF`
- `USDCAD`
- `AUDUSD`
- `NZDUSD`
- `XAUUSD`

Canonical symbols are used in frontend state, URLs, APIs, persistence, intelligence snapshots, framework signals, and feature vectors. Provider-specific mappings are isolated in `backend/forex_intelligence/instruments.py`; for example Yahoo Finance may require `EURUSD=X`, `JPY=X`, or `GC=F`, but those strings should not become application-level identifiers.

`XAUUSD` is explicitly labelled as a gold futures proxy through Yahoo `GC=F` until a true spot-metal provider is connected. The API exposes `is_proxy`, `proxy_for`, and `data_source_type` so the frontend can show that distinction.

## Instrument Registry

Each instrument defines:

- base and quote currency
- display name
- pip size and pip precision
- point size for non-FX instruments
- price precision
- display precision
- minimum price increment
- asset/instrument class
- data source type and proxy metadata
- trading hours and timezone
- provider symbol mappings
- stale-data and minimum-candle thresholds

## APIs

- `GET /api/forex/instruments`
- `GET /api/forex/instruments/{symbol}`
- `GET /api/forex/quotes`
- `GET /api/forex/quotes/{symbol}`
- `GET /api/forex/candles/{symbol}`
- `GET /api/forex-intelligence/{symbol}`
- `GET /api/forex-intelligence/{symbol}/latest`
- `GET /api/forex-intelligence/{symbol}/history`
- `GET /api/forex-intelligence/{symbol}/features`
- `GET /api/forex-intelligence/features/{feature_vector_id}`
- `GET /api/forex-intelligence/{symbol}/overlays`
- `GET /api/forex-frameworks`
- `POST /api/forex-frameworks/analyze`
- `GET /api/forex-frameworks/{symbol}/latest`
- `GET /api/forex-frameworks/{symbol}/comparison`
- `GET /api/forex-frameworks/{symbol}/thesis`

## Frontend

Forex pages use `symbol` and `timeframe` query parameters, for example:

```text
/equity/forex?symbol=EURUSD&timeframe=1h
/forex/chart?symbol=EURUSD&market=FX&timeframe=1h
```

The old `pair` query parameter is still accepted for compatibility, but new navigation should use `symbol`.

## Persistence

Feature vectors are stored in `forex_feature_vectors` with stable vector IDs, feature and engine versions, symbol/timeframe/candle indexes, provider metadata, candle content hashes, quality status, and structured payloads for deterministic explanations.

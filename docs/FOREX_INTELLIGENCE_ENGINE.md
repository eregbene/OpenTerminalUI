# Forex Intelligence Engine

The Forex Intelligence Engine is a read-only forex and metals context layer for Bensim Trading. It supports the canonical symbols `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `USDCAD`, `AUDUSD`, `NZDUSD`, and `XAUUSD`. It is not a strategy, broker, order manager, risk approver, or paper-trading mutator.

## Scope

- Instruments: seven major FX pairs plus `XAUUSD`.
- Asset class: forex, with `XAUUSD` represented as a labelled precious-metal proxy.
- Runtime boundary: read-only analysis provider.
- Consumers: Research, Backtesting, Optimization, Validation, Paper Trading, AI explanations, and chart overlays.

## Architecture

The engine lives in `backend/forex_intelligence` and reuses the existing deterministic `backend/market_structure` package for:

- swings
- BoS
- CHoCH
- MSS
- liquidity levels and sweeps
- fair value gaps
- order blocks
- dealing range
- premium/discount zones
- session overlays

The new layer adds:

- ATR
- historical volatility
- ADR and expected daily range
- volatility state
- EMA trend context
- ADX proxy
- regression slope
- RSI
- stochastic RSI
- MACD
- ROC
- momentum score
- confluence score
- market regime
- deterministic trade explanation
- per-candle feature vectors
- framework-context inputs for the forex framework layer

## Feature Store

Feature vectors are persisted in `forex_feature_vectors` with stable IDs, symbol/timeframe indexes, feature and engine versions, candle content hashes, provider metadata, quality status, and structured payloads for feature, structure, liquidity, volatility, trend, momentum, session, regime, confluence, and explanation data.

## Safety

The engine never:

- submits trades
- changes positions
- changes allocations
- approves risk
- modifies broker state
- modifies paper accounts

## APIs

- `POST /api/forex-intelligence/analyze`
- `GET /api/forex-intelligence/{symbol}`
- `GET /api/forex-intelligence/{symbol}/latest`
- `GET /api/forex-intelligence/{symbol}/history`
- `GET /api/forex-intelligence/{symbol}/features`
- `GET /api/forex-intelligence/features/{feature_vector_id}`
- `GET /api/forex-intelligence/{symbol}/overlays`
- `POST /api/forex-frameworks/analyze`
- `GET /api/forex-frameworks/{symbol}/latest`
- `GET /api/forex-frameworks/{symbol}/comparison`
- `GET /api/forex-frameworks/{symbol}/thesis`

`GET /api/forex-intelligence/eurusd` remains as a compatibility endpoint.

Feature vectors are persisted in `forex_feature_vectors` with stable IDs, symbol/timeframe indexes, feature and engine versions, candle content hashes, provider metadata, quality status, and structured payloads for feature, structure, liquidity, volatility, trend, momentum, session, regime, confluence, and explanation data.

## Limitations

- XAU/USD currently uses Yahoo `GC=F` as an explicitly labelled gold futures proxy.
- News risk is marked unknown until a macro calendar/news-risk adapter is connected.
- Order-flow logic uses deterministic OHLCV proxies, not broker depth or tick-level flow.
- Frameworks that require subjective labeling, centralized forex volume, market profile, auction profile, or advanced pattern confirmation return limited/research statuses until their required evidence is available.

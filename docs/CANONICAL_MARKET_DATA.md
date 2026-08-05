# Canonical Market Data

Implemented in `backend/market_data/models.py`.

Canonical models include:

- `Instrument`, `InstrumentIdentifier`
- `Quote`, `Trade`
- `OHLCVBar`
- `OrderBookSnapshot`, `OrderBookLevel`
- `MarketSession`
- `CorporateAction`
- `FundamentalSnapshot`
- `EconomicObservation`
- `NewsItem`
- `OptionsContract`, `OptionsChain`
- `ProviderMetadata`, `DataQualityMetadata`

Rules:

- Internal timestamps must be timezone-aware UTC.
- Provider source time and receive time are separate.
- Prices and quantities use `Decimal`.
- Provider-specific metadata goes into controlled `extension` fields.
- Existing endpoints are not forced to return these models yet.

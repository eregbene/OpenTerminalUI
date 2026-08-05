# Data Provenance

Canonical provenance uses two models:

- `ProviderMetadata`: provider, original source, fallback source, requested/returned data type and transformation history.
- `DataQualityMetadata`: freshness/status list, origin, entitlement, availability, provider state, timestamps, cache age, stale/simulated/demo flags, quality score and quality flags.

This prevents fallback, stale, cached or simulated data from being represented as realtime.

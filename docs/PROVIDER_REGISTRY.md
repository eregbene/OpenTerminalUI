# Provider Registry

Implemented in `backend/market_data/registry.py`.

The registry records structured metadata for each provider: `provider_id`, display name, asset classes, capabilities, data types, priority, enabled/configured/authenticated state, entitlement status, realtime/historical/streaming/depth flags, rate-limit policy and health snapshot.

Default registrations currently cover:

- `yahoo`
- `nse`
- `fmp`
- `finnhub`
- `fred`
- `internal-demo`

The registry does not expose secrets. API keys are reduced to boolean configured/authenticated state.

Compatibility: the older `backend/services/provider_registry.py` and `backend/adapters/registry.py` remain for legacy endpoints.

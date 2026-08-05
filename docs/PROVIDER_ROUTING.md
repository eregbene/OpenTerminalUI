# Provider Routing

Implemented in `backend/market_data/routing.py`.

Routing is deterministic and priority-based. A request declares capability, asset class, optional symbol, optional pinned provider, configured/entitlement requirements, fallback permission and freshness context.

The router returns:

- selected provider
- candidates considered
- rejected providers with reasons
- whether fallback was used
- selection reason

Fallback is never silent: `internal-demo` and high-priority fallback providers are marked through the routing decision and canonical provenance.

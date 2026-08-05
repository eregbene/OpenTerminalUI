# AI Freshness And Quality

Evidence freshness is domain-aware:

- `CURRENT`: time-sensitive evidence inside its domain threshold.
- `STALE`: time-sensitive evidence older than its threshold.
- `SUPERSEDED`: revoked, expired, invalid, stale, or superseded entity state.
- `IMMUTABLE`: historical research, order, and reconciliation records.
- `INCOMPLETE`: empty entity values.
- `UNKNOWN`: values exist but no reliable timestamp is available.

Quality values:

- `VALID`: complete deterministic evidence.
- `PARTIAL`: evidence has missing structured fields.
- `FALLBACK`: provider or caller fallback evidence.
- `SIMULATED`: paper-trading or simulated execution evidence.
- `DELAYED`: delayed data source.
- `INVALID`: missing or unusable evidence.
- `UNKNOWN`: quality cannot be determined.

Freshness and quality are surfaced in evidence bundles, explanation guardrails, and API responses.

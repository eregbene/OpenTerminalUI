# AI Evidence

Canonical AI evidence is built by `backend/ai_assistant/evidence.py`.

## Evidence Item

Each item contains:

- `source`
- `entity`
- `version`
- `timestamp`
- `freshness`
- `quality`
- `structured_values`

## Freshness

Evidence is marked:

- `fresh` when timestamp age is within 24 hours
- `stale` when older than 24 hours
- `unknown` when no parseable timestamp is supplied

## Quality

Evidence is marked:

- `complete` when supplied values are populated
- `partial` when some values are missing
- `missing` when no values are supplied

## Duplication Rule

The AI evidence layer does not recalculate market structure, research, risk or trading results. It packages deterministic outputs produced elsewhere.

## Phase 9C Bundles

Evidence retrieval now creates canonical bundles persisted under `data/ai_assistant/evidence_bundles.json`.

Each bundle contains `bundle_id`, `request_id`, `primary_entity`, `items`, `lineage`, `missing_evidence`, `warnings`, `created_at` and `content_hash`.

Large fields are bounded before persistence. Duplicate evidence items are removed by content hash.
## Security

Bundles are owner-scoped and expire by policy. Evidence values are allow-listed per entity type and passed through centralized redaction before API responses or audit persistence.

## Phase 9D Grounded Provider Use

Evidence bundles are the only factual input allowed into LLM prompts. Chat requests without a bundle or retrievable entity evidence return the standard insufficient-evidence answer.

Provider output is validated against the evidence bundle before it is returned or streamed.
## Phase 11 Broker Evidence

Broker evidence sources may include broker health, account snapshots, cash, positions, broker orders, executions, commissions and reconciliation results. Evidence must include broker name, paper/live verification, timestamp, freshness, quality and source version.
# Phase 12 Evidence

Portfolio, allocation, valuation, risk, execution, incident, replay, and report records are eligible evidence sources for deterministic explanations.

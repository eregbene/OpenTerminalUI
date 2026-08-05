# AI Evidence Adapters

Adapters live in `backend/ai_assistant/adapters`.

Each adapter returns an `AdapterResult` with:

- `authorization_result`: `ALLOWED`, `DENIED`, `NOT_FOUND`, `SCOPE_MISMATCH`, or `UNSUPPORTED`
- `evidence`: canonical `EvidenceItem` when available
- `warnings`: non-fatal retrieval limitations

Implemented adapters:

- `market_structure.py`: process-local market-structure snapshot lookup.
- `strategy.py`: registered strategy specification and trading deployment lookup.
- `research.py`: research registry lookup for runs, scorecards, and candidates.
- `trading.py`: trading store lookup for risk, orders, fills, positions, snapshots, and reconciliations.

Adapters are read-only. They are retrieval boundaries for deterministic explanation services and are not allowed to execute workflows.

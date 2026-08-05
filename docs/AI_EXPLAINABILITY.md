# AI Explainability

Phase 9B adds deterministic explanation services in `backend/ai_assistant/explanations.py`.

## Supported Explanation Domains

- market structure
- strategy decisions
- research results
- risk evaluations
- paper orders
- paper fills through tool registry evidence
- positions
- reconciliation

## API

- `POST /api/ai/explain/strategy`
- `POST /api/ai/explain/research`
- `POST /api/ai/explain/risk`
- `POST /api/ai/explain/order`
- `POST /api/ai/explain/position`
- `POST /api/ai/explain/reconciliation`

## Guardrails

Every explanation returns:

- `deterministic: true`
- `llm_used: false`
- `read_only: true`
- `execution_authority: none`
- `requires_human_review: true`

## Missing Evidence

Missing entity payloads return a 404 from the API. Stale payloads are explained but clearly marked stale in evidence and summary.

## Phase 9C Grounding

Explanations now prefer adapter-returned evidence, include evidence bundle IDs, and surface freshness/quality limitations. Missing or invalid evidence fails closed.

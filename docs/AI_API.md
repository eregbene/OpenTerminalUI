# AI API

Existing endpoints preserved:

- `POST /api/ai/research-brief`
- `POST /api/ai/explain/strategy`
- `POST /api/ai/explain/research`
- `POST /api/ai/explain/risk`
- `POST /api/ai/explain/order`
- `POST /api/ai/explain/position`
- `POST /api/ai/explain/reconciliation`

Phase 9C evidence endpoints:

- `GET /api/ai/evidence/{bundle_id}`
- `POST /api/ai/evidence/retrieve`
- `POST /api/ai/evidence/lineage`

Evidence retrieval payloads use canonical references:

```json
{
  "domain": "order",
  "entity_type": "paper_order",
  "entity_id": "order_123",
  "entity_version": "3",
  "account_ids": ["acct_123"]
}
```

`entity_type` is optional when it can be inferred from the domain and identifier. All endpoints are read-only and deterministic.

Phase 9D grounded chat endpoints:

- `POST /api/ai/chat`
- `POST /api/ai/chat/stream`
- `GET /api/ai/conversations`
- `GET /api/ai/conversations/{conversation_id}`
- `DELETE /api/ai/conversations/{conversation_id}`
- `POST /api/ai/summarize`
- `POST /api/ai/explain`
- `POST /api/ai/compare`
- `POST /api/ai/investigate`

Chat payloads should provide `message` plus either `evidence_bundle_id` or `domain` and `entity_id`.

```json
{
  "message": "Explain this order",
  "domain": "order",
  "entity_id": "order_123"
}
```

Responses include `answer`, `provider`, `model`, `conversation_id`, `evidence_bundle_id`, `citations`, `grounding`, `token_usage`, `latency_ms` and `finish_reason`.

## Security

All AI endpoints require authenticated user context through the application auth dependency. Clients cannot grant themselves roles, account scopes or research scopes in request bodies.

Errors use stable codes such as `NOT_FOUND`, `FORBIDDEN`, `INVALID_REFERENCE`, `PAYLOAD_TOO_LARGE` and `RATE_LIMITED`, each with a correlation id.

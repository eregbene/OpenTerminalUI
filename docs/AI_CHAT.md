# AI Chat

Phase 9D adds grounded chat APIs around canonical AI evidence.

## Endpoints

- `POST /api/ai/chat`
- `POST /api/ai/chat/stream`
- `POST /api/ai/summarize`
- `POST /api/ai/explain`
- `POST /api/ai/compare`
- `POST /api/ai/investigate`

Chat requests should include either `evidence_bundle_id` or `domain` plus `entity_id`. Requests without verified evidence return the standard insufficient-evidence answer.

The chat layer can explain, summarize, compare and investigate. It cannot place orders, approve risk, approve deployments, change strategies or mutate accounts.

Phase 10 chat calls reserve budget before provider execution and persist provider usage after validation.

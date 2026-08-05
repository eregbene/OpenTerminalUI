# AI API Security Review

Reviewed: `POST /api/ai/research-brief`, all `/api/ai/explain/*` endpoints, `GET /api/ai/evidence/{bundle_id}`, `POST /api/ai/evidence/retrieve`, and `POST /api/ai/evidence/lineage`.

Controls:

- Authentication: `get_current_user`; anonymous requests rejected unless dev auth is explicitly enabled.
- Authorization: server-derived user id/role only; request-body identity/scope fields ignored.
- Identifiers: domain/entity validation plus traversal, encoded traversal, control character and oversized id rejection.
- Request size: 16 KB.
- Response size: 256 KB.
- Rate limits: query/evidence 60/min, explain 40/min, research brief 30/min, lineage 20/min per trusted user/IP/action.
- Audit: actor, endpoint, entity, authorization result, tool, bundle id, timestamp, correlation id, status and content hash.
- Errors: stable code plus correlation id; no stack traces, paths or adapter internals.

Remaining limitations: file-backed storage is not a production multi-node persistence layer; role/account metadata depends on current records; market-structure evidence remains process-local.
## Phase 10 Review Notes

Phase 10 introduces new read-only and research-only APIs:

- `/api/ai/providers*` exposes provider status, usage, budgets, non-secret configuration intent, and health checks.
- `/api/research-agent*` exposes research policies, plans, approvals, task graphs, reports, lineage, kill switches, analysis, recommendations, and hypotheses.
- Provider secret values are never returned by API responses; only redacted metadata is exposed.
- Provider requests are budget-gated before execution and reconciled after completion.
- Streaming responses now emit staged provider events and keep final validated output separate from provisional deltas.
- Research Agent endpoints preserve the no-mutation boundary for trading, broker, portfolio, paper-account, deployment, and risk-approval systems.

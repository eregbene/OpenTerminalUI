# Research Agent API

Implemented endpoints:

- `GET /api/research-agent/status`
- `GET /api/research-agent/policies`
- `POST /api/research-agent/policies`
- `GET /api/research-agent/policies/{policy_id}`
- `POST /api/research-agent/policies/{policy_id}/activate`
- `POST /api/research-agent/policies/{policy_id}/revoke`
- `GET /api/research-agent/plans`
- `POST /api/research-agent/plans`
- `GET /api/research-agent/plans/{plan_id}`
- `PATCH /api/research-agent/plans/{plan_id}`
- `DELETE /api/research-agent/plans/{plan_id}`
- `POST /api/research-agent/plans/{plan_id}/approve`
- `POST /api/research-agent/plans/{plan_id}/reject`
- `POST /api/research-agent/plans/{plan_id}/start`
- `POST /api/research-agent/plans/{plan_id}/pause`
- `POST /api/research-agent/plans/{plan_id}/resume`
- `POST /api/research-agent/plans/{plan_id}/cancel`
- `GET /api/research-agent/plans/{plan_id}/tasks`
- `GET /api/research-agent/plans/{plan_id}/lineage`
- `GET /api/research-agent/plans/{plan_id}/budget`
- `GET /api/research-agent/plans/{plan_id}/reports`
- `POST /api/research-agent/hypotheses`
- `POST /api/research-agent/compare`
- `POST /api/research-agent/analyze`
- `POST /api/research-agent/recommend`
- `GET /api/research-agent/kill-switches`
- `POST /api/research-agent/kill-switches`
- `DELETE /api/research-agent/kill-switches/{switch_id}`

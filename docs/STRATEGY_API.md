# Strategy API

Base path: `/api/strategies`

Endpoints:

- `GET /api/strategies`: list registered strategies.
- `GET /api/strategies/version`: engine name and version.
- `GET /api/strategies/{strategy_id}`: return a strategy spec.
- `POST /api/strategies/validate`: validate a spec.
- `POST /api/strategies/compile`: compile/validate a spec.
- `POST /api/strategies/evaluate`: evaluate a registered strategy or provided spec against bars.
- `GET /api/strategies/evaluations/{evaluation_id}`: retrieve an in-memory evaluation.
- `GET /api/strategies/evaluations/{evaluation_id}/inspector`: inspector summary and proposal overlays.
- `GET /api/strategies/proposals/{proposal_id}`: retrieve an in-memory proposal.

Evaluation storage is process-local in Phase 6.

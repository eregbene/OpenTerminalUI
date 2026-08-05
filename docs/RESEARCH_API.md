# Research API

Canonical strategy research endpoints:

- `POST /api/research/strategy/experiments`
- `GET /api/research/strategy/experiments`
- `GET /api/research/strategy/experiments/{id}`
- `POST /api/research/strategy/backtests`
- `POST /api/research/strategy/optimizations`
- `POST /api/research/strategy/validations`
- `POST /api/research/strategy/workflow`
- `GET /api/research/strategy/scorecards/{id}`
- `GET /api/research/strategy/candidates`
- `GET /api/research/strategy/candidates/{id}`
- `POST /api/research/strategy/candidates/{id}/approve`
- `POST /api/research/strategy/candidates/{id}/reject`
- `GET /api/research/strategy/artifacts/{id}`
- `GET /api/research/strategy/jobs/{id}`

Existing `/api/research/ingest`, `/search` and `/items` remain document-research endpoints.

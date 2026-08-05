# Phase 12 API

Added endpoints:

- `POST /api/portfolio`
- `GET /api/portfolio/dashboard`
- `GET /api/portfolio/management`
- `GET /api/portfolio/{portfolio_id}/dashboard`
- `POST /api/portfolio/{portfolio_id}/strategies`
- `GET /api/portfolio/{portfolio_id}/allocations`
- `POST /api/portfolio/{portfolio_id}/allocations`
- `POST /api/portfolio/{portfolio_id}/snapshots`
- `POST /api/portfolio/accounting/evaluate`
- `GET /api/performance/{portfolio_id}`
- `GET /api/risk/{portfolio_id}/portfolio`
- `POST /api/execution-analytics/evaluate`
- `GET /api/operations/*`
- `GET|POST /api/reports*`
- `POST|GET|DELETE /api/replay/sessions*`
# Phase 12.1 API Update

Canonical plural portfolio routes are now available under `/api/portfolios`. Compatibility routes under `/api/portfolio`, `/api/performance/{portfolio_id}`, and `/api/risk/{portfolio_id}/portfolio` remain available.

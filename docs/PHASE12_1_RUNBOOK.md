# Phase 12.1 Runbook

## Verify Backend

```bash
docker compose --progress plain build backend
docker compose up -d backend redis
docker compose exec -T backend python -m compileall -q backend/portfolio backend/portfolio_risk backend/execution_analytics backend/journal backend/operations backend/reports_phase12 backend/api/routes
docker compose exec -T backend python -m pytest backend/tests/test_portfolio_* backend/tests/test_portfolio_risk_* backend/tests/test_execution_analytics_* backend/tests/test_phase12_1_* --disable-warnings --tb=short -q
docker compose exec -T backend python -m pip check
docker compose exec -T redis redis-cli ping
```

## Verify Frontend

```bash
cd frontend
npm.cmd test -- --run
npm.cmd run build
```

## Safety Check

Confirm no Phase 12 endpoint submits live orders, closes positions, changes broker configuration, activates live accounts, or grants AI mutation authority.

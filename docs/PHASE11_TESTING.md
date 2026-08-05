# Phase 11 Testing

Required checks:

```bash
npm.cmd test -- RendererCore.performance.test.ts --run
npm.cmd test -- --run
npm.cmd run build
docker compose --progress plain build backend
docker compose up -d backend redis
docker compose exec -T backend python -m compileall -q backend/brokers backend/trading backend/api/routes
docker compose exec -T backend python -m pytest backend/tests/test_broker_* backend/tests/test_trading_* --disable-warnings --tb=short
docker compose exec -T backend python -m pytest --disable-warnings --tb=short
```

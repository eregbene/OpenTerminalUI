# Phase 8 Verification Status

Phase 9A required completing the Docker verification path before broad AI work.

## Status

Resolved after Docker Desktop was restarted.

## Dependency Findings

- Docker build context transfer is small; context bloat was not the primary timeout cause.
- Additional generated artifacts are ignored through `.dockerignore`.
- `xgboost`, `hmmlearn`, and `optuna` were moved to optional `backend/requirements-ml.txt`.
- `pyarrow` remains in the default research dependency path because the OHLCV cold-cache parquet tier requires it.
- The Docker image installs `backend/requirements-dev.txt`, so backend pytest is available inside the container.

## Verification Results

- `docker buildx ls`: builder recovered after Docker restart.
- `docker compose --progress plain build`: passed.
- `docker compose up -d backend redis`: passed.
- `docker compose ps`: backend healthy, Redis running.
- `docker compose exec -T backend python --version`: Python 3.11.15.
- `docker compose exec -T backend python -m pip check`: no broken requirements.
- `docker compose exec -T backend python -m pytest --disable-warnings --tb=short`: 730 passed.
- `docker compose exec -T redis redis-cli ping`: PONG.
- `npm.cmd test -- --run`: 91 test files passed, 272 tests passed.
- `npm.cmd run build`: passed.
- `npm.cmd run test:e2e:docker`: 40 passed, 4 skipped.
- Second full Playwright pass: 39 passed, 4 skipped, 1 flaky miss in `correlation-dashboard`; isolated rerun passed.

## Docker E2E Mode

Use:

```powershell
cd frontend
npm.cmd run test:e2e:docker
```

The helper starts/recreates `backend` and `redis` with E2E auth and CORS settings appropriate for the Playwright preview server.

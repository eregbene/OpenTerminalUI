# Testing

## Unified Verification

Windows:

```powershell
.\scripts\verify-all.ps1
```

Unix:

```bash
./scripts/verify-all.sh
```

The script runs frontend tests, frontend build, Docker build/startup, backend dependency check, backend tests, Redis ping, liveness/readiness/API docs checks, and Playwright E2E unless skipped.

## Required Individual Commands

```powershell
cd frontend
npm.cmd test
npm.cmd run build
npm.cmd run test:e2e

cd ..
docker compose build
docker compose up -d
docker compose ps
docker compose exec -T backend python -m pip check
docker compose exec -T backend python -m pytest --disable-warnings --tb=short
```
# Docker Backend E2E Mode

Windows hosts do not need Python 3.11 for Playwright when Docker is available.

1. Build and start the supported backend:

```powershell
docker compose build
docker compose up -d backend redis
```

2. Run Playwright against the Docker backend:

```powershell
cd frontend
npm.cmd run test:e2e:docker
```

This sets `E2E_BACKEND_MODE=docker` and uses `http://127.0.0.1:8000` by default. Override with `E2E_BACKEND_URL` or `E2E_BACKEND_PORT` if needed. The mode fails with a clear message when the Docker backend is not reachable instead of trying to start the backend with host Python.
## Phase 10 Verification

Phase 10 verification covers:

- Provider production hardening tests in `backend/tests/test_ai_provider_production_phase10.py`.
- Research Agent policy, approval, execution, report, safety, and tenant-isolation tests in `backend/tests/test_research_agent_phase10.py`.
- Frontend Research Agent page coverage in `frontend/src/__tests__/ResearchAgentPage.test.tsx`.
- Playwright smoke coverage for `/equity/research-agent` in `frontend/tests/e2e/research-agent.spec.ts`.

Required commands:

```bash
python -m compileall -q backend/ai_assistant backend/ai_provider backend/ai_secrets backend/research_agent backend/api/routes
python -m pytest backend/tests/test_ai_* backend/tests/test_research_agent_* --disable-warnings --tb=short
python -m pytest --disable-warnings --tb=short
npm.cmd test -- --run
npm.cmd run build
```
## Phase 11 Tests

Phase 11 adds broker tests in `backend/tests/test_broker_phase11.py`, Broker Ops UI coverage in `frontend/src/__tests__/BrokerOperationsPage.test.tsx`, and Playwright smoke coverage in `frontend/tests/e2e/broker-operations.spec.ts`.
# Phase 12 Tests

Focused Phase 12 tests cover accounting invariants, allocation validation, risk metrics, and execution analytics. See `docs/PHASE12_TESTING.md`.

# Phase FX-3 Tests

Focused FX-3 tests cover XAU/USD metadata, forex quote exposure, framework registry contracts, framework signal persistence, confluence/thesis generation, unsupported symbols, deterministic execution order, and the Forex page framework panel. See `docs/FOREX_FRAMEWORK_TESTING.md`.

# Phase FX-4 Tests

Focused FX-4 tests cover strategy registry defaults, candidate idempotency, XAU/USD execution blocking, stale-data rejection, risk sizing, risk rejection, and the EUR/USD candidate-to-OMS-to-fill vertical slice.

# Phase FX-5 Tests

Focused FX-5 tests cover mocked IBKR connection/account verification, live-account rejection, EURUSD/GBPUSD/USDJPY contract verification, XAU/USD contract blocking, broker order event ledger, idempotent duplicate prevention, reconciliation, restart recovery gate, frontend IBKR status panel, and frontend contract status.
# FX-5B Testing

Focused tests cover IBKR database persistence and real-mode fixture rejection. Real TWS/Gateway paper acceptance must be run manually with a verified paper account.

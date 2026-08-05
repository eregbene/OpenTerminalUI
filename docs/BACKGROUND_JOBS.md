# Background Jobs

## Current Job Types

- FastAPI `BackgroundTasks` for portfolio backtests and model lab actions.
- In-process services for prefetch, instruments loading, news ingestion, PCR snapshots, scanner alerts, market data hub, and paper trading.
- Async tasks for WebSocket streams and polling loops.

## Standard Model

`backend/core/contracts/jobs.py` defines:

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `retrying`

New jobs should use `JobRecord` semantics even if persistence remains domain-specific.
# Phase 7 Research Jobs

The Phase 7 vertical slice models durable job ids and cancellation boundaries, but executes small deterministic jobs synchronously through the API for testability. Large optimization/background execution remains future work using the existing job service patterns.


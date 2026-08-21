"""One-off catch-up job for the Adaptive Trade Manager's own auto-replay counterfactual engine
(backend/adaptive_management/service.py::_auto_replay_recently_closed) -- NOT the same system as
Historical Intelligence's fingerprint corpus. That function's own pending-position query is NOT
account-scoped (processes the global backlog regardless of which account_id constructed the
service), so this drives ONE service instance in a loop, bypassing its cooldown guard (a
deliberate one-off catch-up run, not the live monitor's steady-state cadence) and requesting the
max batch size (50) per iteration, until the backlog clears or a safety cap is hit.

Safe to run any time -- this function is documented analysis-only (never calls the broker, never
mutates the original trade journal), and only became productive for most of the backlog after this
session's timeframe-corruption fix (backend/adaptive_management/service.py::_parse_bsm_comment)
and the one-time backfill of 579 already-corrupted AdaptivePositionStateORM.timeframe values."""
from __future__ import annotations

import asyncio
import os
import time

from backend.adaptive_management import service as adaptive_service
from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.shared.db import SessionLocal

os.environ.setdefault("ADAPTIVE_AUTO_REPLAY_BATCH_SIZE", "50")
MAX_ITERATIONS = 200  # 200 * 50 = 10,000 positions -- well above the real backlog


def _pending_count() -> int:
    with SessionLocal() as db:
        return db.query(AdaptivePositionStateORM).filter(
            AdaptivePositionStateORM.closed_detected_at.isnot(None), AdaptivePositionStateORM.replay_completed_at.is_(None)
        ).count()


async def main() -> None:
    svc = adaptive_service.AdaptiveManagementService()
    total_before = _pending_count()
    print(f"total un-replayed closed positions before catch-up: {total_before}", flush=True)

    for i in range(MAX_ITERATIONS):
        before = _pending_count()
        if before == 0:
            print(f"backlog cleared after {i} batches", flush=True)
            break

        svc._last_auto_replay_at = None  # bypass the steady-state cooldown for this one-off run
        with SessionLocal() as db:
            await svc._auto_replay_recently_closed(db)

        after = _pending_count()
        print(f"batch {i}: pending {before} -> {after}", flush=True)
        if after == before:
            print("no progress this batch (remaining rows likely lack deal data or candle coverage) -- stopping", flush=True)
            break
        time.sleep(0.2)
    else:
        print("hit MAX_ITERATIONS safety cap", flush=True)

    total_after = _pending_count()
    print(f"total un-replayed closed positions after catch-up: {total_after} (was {total_before})", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

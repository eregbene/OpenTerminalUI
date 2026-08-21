"""Breadth-first Adaptive Historical Intelligence backfill driver: cycles through ALL strategies
present in the trusted entry-side corpus in bounded rounds (default 400 trades/strategy/round)
rather than exhausting one strategy before starting the next. Directive: "Do not stop after
backfilling strategy #2. Continue through all eligible strategies." mtfai1 already has 53,000+
states from the prior (now-superseded) mtfai1-only-first driver -- this one gives every OTHER
strategy real coverage sooner, which is what the fragmentation/effective-N investigation actually
needs (breadth across strategy/symbol/direction, not more depth on one strategy alone).

Fully resumable/idempotent per strategy (same run_adaptive_backfill()/_checkpoint_progress()
machinery, unchanged) -- safe to stop and restart at any round boundary with zero lost or
duplicated work.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func

from backend.historical_intelligence.adaptive_backfill import run_adaptive_backfill
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
from backend.shared.db import SessionLocal

_TRADES_PER_STRATEGY_PER_ROUND = 400


def _real_strategies() -> list[str]:
    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM.anchor_strategy, func.count())
            .filter(HistoricalPatternFingerprintORM.source_evaluation_id.is_(None))
            .group_by(HistoricalPatternFingerprintORM.anchor_strategy)
            .order_by(func.count().desc())
            .all()
        )
    return [r[0] for r in rows]


async def main() -> None:
    strategies = _real_strategies()
    print(f"round-robin strategies: {strategies}", flush=True)
    exhausted: set[str] = set()
    round_num = 0
    while len(exhausted) < len(strategies):
        round_num += 1
        for strategy in strategies:
            if strategy in exhausted:
                continue
            result = await run_adaptive_backfill(anchor_strategy=strategy, limit=_TRADES_PER_STRATEGY_PER_ROUND, commit_batch_size=20, compute_structure=True, resume=True, progress_every=0)
            trades = result.get("trades_processed", 0)
            print(f"round={round_num} strategy={strategy} -> {result}", flush=True)
            if trades == 0:
                exhausted.add(strategy)
    print("ALL STRATEGIES EXHAUSTED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

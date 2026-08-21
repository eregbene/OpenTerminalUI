"""Phase 6/7: fresh parity batch for the 3 positive-edge strategies, re-run with the
deterministic-ordering fix in place, then recompute trust via the existing, unchanged
architecture (trust_gating.recompute_trust) -- never force ACTIVE."""
import asyncio

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.historical_intelligence.parity import verify_parity
from backend.historical_intelligence.trust_gating import recompute_trust
from backend.shared.db import SessionLocal

STRATEGIES = ("vwap_reversion", "mean_reversion", "trend_pullback")
_BATCH_PER_STRATEGY = 60
_SLEEP_SECONDS = 0.3


async def main() -> None:
    with SessionLocal() as db:
        ids_by_strategy = {
            s: [r[0] for r in db.query(MT5CandidateEvaluationORM.evaluation_id).filter(MT5CandidateEvaluationORM.strategy == s).order_by(MT5CandidateEvaluationORM.created_at.desc()).limit(_BATCH_PER_STRATEGY).all()]
            for s in STRATEGIES
        }
    for s, ids in ids_by_strategy.items():
        print(f"{s}: {len(ids)} real evaluations queued", flush=True)

    verdict_counts: dict[str, dict[str, int]] = {s: {} for s in STRATEGIES}
    for strategy, ids in ids_by_strategy.items():
        for i, eval_id in enumerate(ids):
            try:
                result = await verify_parity(eval_id)
                verdict = result.get("verdict", "UNKNOWN")
                verdict_counts[strategy][verdict] = verdict_counts[strategy].get(verdict, 0) + 1
            except Exception as exc:
                verdict_counts[strategy][f"ERROR:{exc.__class__.__name__}"] = verdict_counts[strategy].get(f"ERROR:{exc.__class__.__name__}", 0) + 1
            await asyncio.sleep(_SLEEP_SECONDS)
        print(f"{strategy}: {verdict_counts[strategy]}", flush=True)

    print("\n=== Recomputing trust (existing, unchanged architecture) ===", flush=True)
    results = recompute_trust()
    for strategy in STRATEGIES:
        r = results.get(strategy)
        if r:
            print(strategy, "->", r.get("trust_state"), "|", r.get("reason"), flush=True)
        else:
            print(strategy, "-> no trust row computed (no checks on record)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

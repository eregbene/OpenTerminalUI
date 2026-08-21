"""Phase 1/3 reproduction: replay the same real evaluation_id N times, hashing every stage's
inputs/outputs, to determine whether replay is non-deterministic and (if so) whether the fix in
replay.py (deterministic secondary sort key on the revision/canonical candle queries) resolves
it. Snapshot-tier evaluations are the control group (should already be deterministic by
construction -- exact rows persisted at decision time, no revision-selection query involved)."""
import asyncio
import hashlib
import json

from backend.historical_intelligence.orm import HistoricalReplayParityCheckORM
from backend.historical_intelligence.replay import replay_for_evaluation
from backend.shared.db import SessionLocal


def _candle_set_hash(candles) -> str:
    rows = []
    for c in candles:
        if isinstance(c, dict):
            rows.append({"time": str(c.get("time")), "o": str(c.get("open")), "h": str(c.get("high")), "l": str(c.get("low")), "c": str(c.get("close"))})
        else:
            rows.append({"time": str(c.time), "o": str(c.open), "h": str(c.high), "l": str(c.low), "c": str(c.close)})
    payload = json.dumps(rows, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def probe(evaluation_id: str, runs: int = 10) -> None:
    print(f"=== evaluation_id={evaluation_id} runs={runs} ===", flush=True)
    regimes, sources, m15_hashes, h1_hashes, h4_hashes = [], [], [], [], []
    for i in range(runs):
        result = await replay_for_evaluation(evaluation_id)
        regimes.append(result.get("regime"))
        sources.append(result.get("source"))
        ctx = result.get("_ctx")
        if ctx is not None:
            m15_hashes.append(_candle_set_hash(ctx.m15_rows if hasattr(ctx, "m15_rows") else []))
        families = result.get("families_candidates") or []
        print(f"  run {i}: source={result.get('source')} regime={result.get('regime')} status={result.get('status')} n_families={len(families)}", flush=True)

    print(f"  DISTINCT regimes: {set(regimes)}", flush=True)
    print(f"  DISTINCT sources: {set(sources)}", flush=True)
    if m15_hashes:
        print(f"  DISTINCT m15 candle-set hashes: {len(set(m15_hashes))} (1 = deterministic)", flush=True)
    print(f"  {'DETERMINISTIC' if len(set(regimes)) <= 1 else 'NON-DETERMINISTIC'}", flush=True)


async def main() -> None:
    with SessionLocal() as db:
        # A snapshot-tier evaluation, if one exists, as the control group.
        snap_check = db.query(HistoricalReplayParityCheckORM).filter(HistoricalReplayParityCheckORM.diff_detail.op("->>")("replay_source") == "SNAPSHOT").first()
        # RECONSTRUCTED-tier evaluations for each of the 3 positive strategies -- the real,
        # previously-diagnosed non-determinism case.
        recon_checks = {
            s: db.query(HistoricalReplayParityCheckORM).filter(HistoricalReplayParityCheckORM.strategy_id == s, HistoricalReplayParityCheckORM.verdict == "REPLAY_MISSING").first()
            for s in ("vwap_reversion", "mean_reversion", "trend_pullback")
        }

    if snap_check is not None:
        print("--- SNAPSHOT-tier control group ---", flush=True)
        await probe(snap_check.live_evaluation_id, runs=8)
    else:
        print("no SNAPSHOT-tier parity check found to use as control", flush=True)

    print()
    print("--- RECONSTRUCTED-tier (previously diagnosed as unstable) ---", flush=True)
    for strategy, check in recon_checks.items():
        if check is None:
            print(f"{strategy}: no REPLAY_MISSING check found", flush=True)
            continue
        await probe(check.live_evaluation_id, runs=8)


if __name__ == "__main__":
    asyncio.run(main())

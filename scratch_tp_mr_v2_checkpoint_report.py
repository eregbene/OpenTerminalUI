"""trend_pullback/mean_reversion post-fix forward-validation checkpoint report.

Companion to scratch_mtfai1_v2_checkpoint_report.py -- same pattern, different cutover. Reports
the post-confirmation-bonus-fix, post-trend_quality_score population (created_at >=
CONFIDENCE_FIX_AT) for both strategies: confidence-band calibration curve (with MFE/MAE/+0.5R/
+1R/+2R), trend_multi_timeframe source tracking (confirms trend_quality_score is actually being
computed, not silently falling back to the generic ranking_score), and strategy/symbol_performance
neutrality tracking (should stay ~neutral until enough post-fix trades accumulate under the new
STRATEGY_VERSION_CUTOVER["trend_pullback"] entry).

Re-run this at 20/50/100 resolved post-fix trades per strategy -- it's a pure read, cheap to run
any time. Honest by construction: reports "too early" rather than fabricating a curve when there
isn't enough resolved data yet.
"""
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone

from backend.shared.db import SessionLocal
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM

CONFIDENCE_FIX_AT = datetime(2026, 8, 25, 9, 35, 0, tzinfo=timezone.utc)
STRATEGIES = ("trend_pullback", "mean_reversion")

CONF_BUCKETS = [(0, 60, "<60"), (60, 65, "60-65"), (65, 70, "65-70"), (70, 75, "70-75"),
                (75, 80, "75-80"), (80, 85, "80-85"), (85, 90, "85-90"), (90, 101, "90+")]


def comp(row, name):
    for c in (row.components or []):
        if c.get("name") == name:
            return c
    return None


def stats(vals, mfes=None, maes=None):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    wins = [x for x in vals if x > 0]
    losses = [x for x in vals if x < 0]
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    out = {"n": n, "wr": round(len(wins) / n, 3), "exp": round(st.fmean(vals), 4), "pf": round(gw / gl, 3) if gl else None}
    if mfes:
        m = [x for x in mfes if x is not None]
        if m:
            out["reach_0_5r"] = round(sum(1 for x in m if x >= 0.5) / len(m), 3)
            out["reach_1r"] = round(sum(1 for x in m if x >= 1.0) / len(m), 3)
            out["reach_2r"] = round(sum(1 for x in m if x >= 2.0) / len(m), 3)
            out["avg_mfe"] = round(st.fmean(m), 3)
    if maes:
        a = [x for x in maes if x is not None]
        if a:
            out["avg_mae"] = round(st.fmean(a), 3)
    return out


def main():
    with SessionLocal() as db:
        for strategy_id in STRATEGIES:
            print(f"\n{'='*72}\n{strategy_id} post-fix population (created_at >= {CONFIDENCE_FIX_AT})\n{'='*72}")
            rows = db.query(MT5CandidateEvaluationORM).filter(
                MT5CandidateEvaluationORM.strategy == strategy_id,
                MT5CandidateEvaluationORM.created_at >= CONFIDENCE_FIX_AT,
            ).all()
            resolved = [r for r in rows if r.hypothetical_r is not None or r.realized_r is not None]
            executed = [r for r in rows if r.outcome_type == "EXECUTED"]
            print(f"total candidate rows: {len(rows)} | EXECUTED: {len(executed)} | resolved: {len(resolved)}")

            if not resolved:
                print("No resolved outcomes yet -- too early for the calibration report.")
                confs = [r.overall_confidence for r in rows if r.overall_confidence is not None]
                if confs:
                    print(f"  confidence so far: n={len(confs)} min={min(confs):.1f} max={max(confs):.1f} mean={st.fmean(confs):.1f}")
                sources = Counter((comp(r, "trend_multi_timeframe") or {}).get("inputs", {}).get("source", "MISSING") for r in rows)
                print(f"  trend_multi_timeframe source so far: {dict(sources)}")
                continue

            def r_of(row):
                return row.realized_r if row.realized_r is not None else row.hypothetical_r

            print("\nconfidence band calibration:")
            grouped = defaultdict(list)
            for row in resolved:
                c = row.overall_confidence
                if c is None:
                    continue
                for lo, hi, name in CONF_BUCKETS:
                    if lo <= c < hi:
                        grouped[name].append(row)
                        break
            for _, _, name in CONF_BUCKETS:
                bucket_rows = grouped.get(name, [])
                if bucket_rows:
                    print(f"  {name}: {stats([r_of(r) for r in bucket_rows], [r.mfe_r for r in bucket_rows], [r.mae_r for r in bucket_rows])}")

            print("\ntrend_multi_timeframe source (should be strategy_specific, not generic_fallback, given the flag is on):")
            sources = Counter((comp(r, "trend_multi_timeframe") or {}).get("inputs", {}).get("source", "MISSING") for r in rows)
            print(f"  {dict(sources)}")

            print("\nstrategy_performance / symbol_performance neutrality (should still be ~neutral this early post-cutover):")
            for name in ("strategy_performance", "symbol_performance"):
                scores = [comp(r, name)["score"] for r in rows if comp(r, name)]
                neutral = sum(1 for s in scores if s == 60.0)
                print(f"  {name}: n={len(scores)}, neutral(60)={neutral}, non-neutral={len(scores)-neutral}")


if __name__ == "__main__":
    main()

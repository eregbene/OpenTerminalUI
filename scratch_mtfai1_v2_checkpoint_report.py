"""MTFAI1 V2 forward-validation checkpoint report.

Reports the post-fix (V2-activation-onward, and separately post-confidence-architecture-fix-
onward) mtfai1 candidate population: confidence-band calibration curve, RR-quality bottleneck
detection for FVG/OB-target candidates, and strategy_performance/symbol_performance neutrality
tracking. Re-run this at 20/50/100 resolved MTFAI1 V2 trades (and any time for a quick check --
it's cheap, pure read).

Two cutover timestamps matter:
  V2_ACTIVATION_AT   -- when MT5_MTFAI1_V2_ENABLED went live (new geometry/eligibility/MFE).
  CONFIDENCE_FIX_AT  -- when the confidence-architecture fixes (real trend_quality_score,
                        version-scoped strategy/symbol_performance, HI-neutral-for-V2) went live.
Everything reported here is filtered to created_at >= CONFIDENCE_FIX_AT, since that's the actual
"new scoring architecture" population the user wants tracked separately from pre-fix data.
"""
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone

from backend.shared.db import SessionLocal
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM

V2_ACTIVATION_AT = datetime(2026, 8, 24, 18, 44, 0, tzinfo=timezone.utc)
CONFIDENCE_FIX_AT = datetime(2026, 8, 25, 7, 40, 0, tzinfo=timezone.utc)  # trend_quality_score + symbol_performance hierarchy deploy

CONF_BUCKETS = [(0, 65, "<65"), (65, 70, "65-70"), (70, 75, "70-75"), (75, 80, "75-80"), (80, 85, "80-85"), (85, 90, "85-90"), (90, 101, "90+")]


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
        all_rows = db.query(MT5CandidateEvaluationORM).filter(
            MT5CandidateEvaluationORM.strategy == "mtfai1",
            MT5CandidateEvaluationORM.created_at >= CONFIDENCE_FIX_AT,
        ).all()

    print(f"=== MTFAI1 V2 post-confidence-fix population (created_at >= {CONFIDENCE_FIX_AT}) ===")
    print(f"total candidate rows: {len(all_rows)}")
    executed = [r for r in all_rows if r.outcome_type == "EXECUTED"]
    resolved = [r for r in all_rows if r.hypothetical_r is not None or r.realized_r is not None]
    print(f"EXECUTED: {len(executed)} | resolved (SHADOW+EXECUTED, outcome known): {len(resolved)}")

    if not resolved:
        print("\nNo resolved outcomes yet -- too early for the calibration/checkpoint report. Confidence/rejection distribution only:")
        confs = [r.overall_confidence for r in all_rows if r.overall_confidence is not None]
        if confs:
            print(f"  confidence: n={len(confs)} min={min(confs):.1f} max={max(confs):.1f} mean={st.fmean(confs):.1f}")
        reasons = Counter(tuple(sorted(r.rejection_reasons or [])) for r in all_rows)
        for r, n in reasons.most_common(10):
            print(f"  {n:>4}x {r}")
        return

    def r_of(row):
        return row.realized_r if row.realized_r is not None else row.hypothetical_r

    print("\n=== Final confidence calibration (post-fix population) ===")
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
        rows = grouped.get(name, [])
        s = stats([r_of(r) for r in rows], [r.mfe_r for r in rows], [r.mae_r for r in rows])
        print(f"  {name}: {s}")

    executed_resolved = [r for r in executed if r.realized_r is not None]
    if executed_resolved:
        print(f"\n=== EXECUTED-only checkpoint (n={len(executed_resolved)}) ===")
        print(f"  {stats([r.realized_r for r in executed_resolved], [r.mfe_r for r in executed_resolved], [r.mae_r for r in executed_resolved])}")

    # RR-quality bottleneck check: candidates with confidence in [65,75) where reward_risk_quality
    # scored low AND risk_reward sits near 1.0 (V2's FVG/OB floor) -- would this candidate have
    # cleared 75 if RR had scored neutral (60) instead of its actual (low) score?
    print("\n=== reward_risk_quality bottleneck check (confidence 65-75, RR near V2's 1.0R floor) ===")
    suspects = []
    for row in all_rows:
        if row.overall_confidence is None or not (65 <= row.overall_confidence < 75):
            continue
        rr_comp = comp(row, "reward_risk_quality")
        if rr_comp is None:
            continue
        rr_value = (rr_comp.get("inputs") or {}).get("risk_reward")
        if rr_value is None or rr_value > 1.6:
            continue  # not a V2-floor-shaped RR, not a suspect
        weight = rr_comp.get("weight") or 0.14
        hypothetical_gain = (60.0 - rr_comp["score"]) * weight  # what confidence would be if RR scored neutral instead
        would_clear_75 = row.overall_confidence + hypothetical_gain >= 75
        suspects.append((row, rr_comp["score"], rr_value, hypothetical_gain, would_clear_75))
    print(f"  candidates in [65,75) with RR<=1.6 (V2-floor-shaped): {len(suspects)}")
    would_clear = [s for s in suspects if s[4]]
    print(f"  of those, would clear 75 if RR scored neutral instead of its actual score: {len(would_clear)}")
    for row, rr_score, rr_value, gain, cleared in suspects[:8]:
        outcome = r_of(row)
        print(f"    {row.symbol} {row.direction} conf={row.overall_confidence:.1f} rr_score={rr_score:.1f} rr={rr_value:.2f} would_gain={gain:.2f} would_clear_75={cleared} outcome_r={outcome}")

    # strategy_performance / symbol_performance neutrality tracking
    print("\n=== strategy_performance / symbol_performance neutrality (should still be ~all neutral=60 this early) ===")
    for name in ("strategy_performance", "symbol_performance"):
        scores = [comp(r, name)["score"] for r in all_rows if comp(r, name)]
        neutral = sum(1 for s in scores if s == 60.0)
        print(f"  {name}: n={len(scores)}, neutral(60)={neutral}, non-neutral={len(scores)-neutral}")
        non_neutral_vals = sorted(set(s for s in scores if s != 60.0))
        if non_neutral_vals:
            print(f"    non-neutral values seen: {non_neutral_vals[:10]}")

    # HI status tracking (should be NEUTRAL/MTFAI1_V2_HI_NOT_YET_VERSION_COMPATIBLE for every V2 candidate)
    print("\n=== Historical Intelligence status (should be 100% NEUTRAL for MTFAI1 V2) ===")
    hi_statuses = Counter((r.historical_intelligence or {}).get("status", "MISSING") for r in all_rows)
    print(f"  {dict(hi_statuses)}")
    non_neutral_hi = [r for r in all_rows if r.historical_intelligence and r.historical_intelligence.get("status") not in ("NEUTRAL", None)]
    if non_neutral_hi:
        print(f"  WARNING: {len(non_neutral_hi)} candidates got a non-neutral HI evaluation -- investigate whether the V2 gate is being bypassed.")

    # trend_quality_score sanity: confirm it's being computed (not silently falling back to raw ranking_score)
    print("\n=== trend_multi_timeframe source (should be strategy_specific for V2, not generic_fallback) ===")
    sources = Counter((comp(r, "trend_multi_timeframe") or {}).get("inputs", {}).get("source", "MISSING") for r in all_rows)
    print(f"  {dict(sources)}")


if __name__ == "__main__":
    main()

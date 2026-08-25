"""Bensim -- Activate All Strategy Families in DEMO, Part 12: unified per-strategy DEMO dashboard.

Covers every strategy currently ACTIVE_MT5 (real order authority): mtfai1, trend_pullback,
mean_reversion (Tier A), smc_continuation, liquidity_sweep_reversal, vwap_reversion (Tier B),
support_resistance_bounce, session_breakout, breakout, ema_trend (Tier C). Reports per strategy,
never pooled: CANDIDATES, CONF>=THRESHOLD, EXECUTED, CLOSED, WIN RATE, EXPECTANCY R, PF, NET R,
MAX DD, AVG MFE, AVG MAE, STATIC RESULT, MANAGED RESULT, MANAGER DELTA. Checkpoints noted at
20/50/100 closed trades per strategy (reports current N regardless, never fabricates a verdict
before the sample is there).

Cheap, pure read -- safe to re-run any time.
"""
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.mt5_strategies.models import activation_status
from backend.shared.db import SessionLocal

TIER = {
    "mtfai1": "A", "trend_pullback": "A", "mean_reversion": "A",
    "vwap_reversion": "B", "liquidity_sweep_reversal": "B", "smc_continuation": "B",
    "support_resistance_bounce": "C", "breakout": "C", "ema_trend": "C", "session_breakout": "C",
}
CONF_THRESHOLD = 75.0

# 2026-08-25 Bensim -- Activate All Strategy Families in DEMO, Stage A: real DEMO reactivation
# instant for the 7 Tier B/C strategies (smc_continuation/liquidity_sweep_reversal/
# vwap_reversion/support_resistance_bounce/session_breakout/breakout/ema_trend) -- most of the
# EXECUTED/CLOSED history the plain query below finds for these strategies PREDATES this
# reactivation (they were briefly ACTIVE_MT5 before the 2026-08-21 Priority-4 demotion to
# SHADOW_MT5) -- real, valid evidence for the tier assignment, but NOT fresh forward evidence
# from today's reactivation. Report both: ALL_TIME (what's shown by default) and SINCE_
# REACTIVATION (the genuinely new checkpoint population) once enough post-reactivation trades
# exist.
REACTIVATION_AT = datetime(2026, 8, 25, 13, 20, 0, tzinfo=timezone.utc)
REACTIVATED_STRATEGIES = {"smc_continuation", "liquidity_sweep_reversal", "vwap_reversion", "support_resistance_bounce", "session_breakout", "breakout", "ema_trend"}


def r_of(row):
    return row.realized_r if row.realized_r is not None else row.hypothetical_r


def stats_block(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    equity, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return {"n": n, "wr": round(len(wins) / n, 3), "exp": round(st.fmean(vals), 4), "pf": round(gw / gl, 3) if gl else None, "net_R": round(sum(vals), 3), "max_dd": round(dd, 3)}


def main():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for strategy_id, tier in TIER.items():
            status = activation_status(strategy_id)
            rows = db.query(MT5CandidateEvaluationORM).filter(MT5CandidateEvaluationORM.strategy == strategy_id).all()
            executed = [r for r in rows if r.outcome_type == "EXECUTED"]
            closed = [r for r in executed if r_of(r) is not None]
            conf_pass = [r for r in rows if (r.overall_confidence or 0) >= CONF_THRESHOLD]

            print(f"\n{'='*100}\n{strategy_id}  [Tier {tier}]  activation={status}\n{'='*100}")
            print(f"  CANDIDATES: {len(rows)}  |  CONF>={CONF_THRESHOLD}: {len(conf_pass)}  |  EXECUTED: {len(executed)}  |  CLOSED: {len(closed)}")
            if strategy_id in REACTIVATED_STRATEGIES:
                since_closed = [r for r in closed if r.created_at and r.created_at >= REACTIVATION_AT]
                print(f"  (of which SINCE today's reactivation @ {REACTIVATION_AT.isoformat()}: closed={len(since_closed)} -- the rest predates the 2026-08-21 SHADOW demotion)")

            if closed:
                vals = [r_of(r) for r in closed]
                s = stats_block(vals)
                mfes = [r.mfe_r for r in closed if r.mfe_r is not None]
                maes = [r.mae_r for r in closed if r.mae_r is not None]
                print(f"  WIN RATE: {s['wr']}  EXPECTANCY_R: {s['exp']:+.4f}  PF: {s['pf']}  NET_R: {s['net_R']:+.3f}  MAX_DD: {s['max_dd']:+.3f}")
                if mfes:
                    print(f"  AVG_MFE: {st.fmean(mfes):.3f}")
                if maes:
                    print(f"  AVG_MAE: {st.fmean(maes):.3f}")
                for checkpoint in (20, 50, 100):
                    marker = "REACHED" if s["n"] >= checkpoint else f"not yet ({s['n']}/{checkpoint})"
                    print(f"  checkpoint @{checkpoint} closed: {marker}")
            else:
                print("  No closed trades yet -- too early for outcome stats.")

            # STATIC vs MANAGED vs DELTA -- match AdaptivePositionStateORM closed positions to
            # their originating candidate's static/unmanaged outcome_r, same technique as the
            # earlier per-strategy checkpoint tool.
            pos_rows = db.query(AdaptivePositionStateORM).filter(
                AdaptivePositionStateORM.strategy_id == strategy_id,
                AdaptivePositionStateORM.closed_detected_at.isnot(None),
            ).all()
            if pos_rows:
                deltas = []
                for p in pos_rows:
                    managed_r = (p.max_achieved_r or 0.0) - (p.current_giveback_r or 0.0)
                    candidate_match = min(
                        (r for r in closed if r.symbol == p.symbol and r.direction == p.direction),
                        key=lambda r: abs((r.created_at - p.opened_at).total_seconds()) if p.opened_at and r.created_at else float("inf"),
                        default=None,
                    )
                    static_r = r_of(candidate_match) if candidate_match else None
                    if static_r is not None:
                        deltas.append((static_r, managed_r, managed_r - static_r))
                if deltas:
                    avg_static = st.fmean(d[0] for d in deltas)
                    avg_managed = st.fmean(d[1] for d in deltas)
                    avg_delta = st.fmean(d[2] for d in deltas)
                    print(f"  STATIC_RESULT: {avg_static:+.3f}R  MANAGED_RESULT: {avg_managed:+.3f}R  MANAGER_DELTA: {avg_delta:+.3f}R  (n={len(deltas)} matched)")
                else:
                    print(f"  {len(pos_rows)} closed managed position(s), but none matched to a static candidate outcome yet.")
            else:
                print("  No closed managed positions yet.")

    print(f"\n(dashboard generated {now.isoformat()})")


if __name__ == "__main__":
    main()

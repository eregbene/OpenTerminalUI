"""Forward DEMO execution checkpoint -- MTFAI1 V2 and trend_pullback V2, tracked separately.

Run this at any time (cheap, pure read); the user wants it specifically re-run at 20/50/100
closed real trades per strategy. Reports, for each strategy:

  1. Executed-trade table: N | win rate | expectancy R | PF | net R | max DD | avg winner |
     avg loser | avg MFE | avg MAE | confidence band | symbol | regime | manager actions.
  2. STATIC SL/TP RESULT -> ACTUAL MANAGED RESULT -> ADAPTIVE MANAGER DELTA, matched via
     AdaptivePositionStateORM (real managed outcome) against MT5CandidateEvaluationORM (real
     static/unmanaged outcome_r from the outcome resolver, which evaluates the ORIGINAL
     stop/target independent of what the Adaptive Manager did to the live position).
  3. Live rejection funnel: candidate -> raw strategy valid -> confidence>=75 -> portfolio/risk
     passed -> broker passed -> executed, so a stalled trade count can be attributed to the
     strategy, confidence, risk gates, or market conditions rather than guessed at.

Never fabricates a verdict from too little data -- reports real N at every stage and says so
explicitly when a stage has too few resolved rows to summarize.
"""
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal

MTFAI1_V2_ACTIVATION_AT = datetime(2026, 8, 24, 18, 44, 0, tzinfo=timezone.utc)
TREND_PULLBACK_V2_CUTOVER_AT = datetime(2026, 8, 25, 9, 35, 0, tzinfo=timezone.utc)
STRATEGIES = {"mtfai1": MTFAI1_V2_ACTIVATION_AT, "trend_pullback": TREND_PULLBACK_V2_CUTOVER_AT}

CONF_THRESHOLD = 75.0


def r_of(row):
    return row.realized_r if row.realized_r is not None else row.hypothetical_r


def _confidence_band(c):
    if c is None:
        return "unknown"
    for lo, hi, name in [(90, 101, "90+"), (85, 90, "85-90"), (80, 85, "80-85"), (75, 80, "75-80"),
                          (70, 75, "70-75"), (65, 70, "65-70"), (0, 65, "<65")]:
        if lo <= c < hi:
            return name
    return "unknown"


def max_drawdown(sequence_r):
    """Running peak-to-trough drawdown over the R sequence in chronological order."""
    if not sequence_r:
        return None
    equity = 0.0
    peak = 0.0
    worst_dd = 0.0
    for r in sequence_r:
        equity += r
        peak = max(peak, equity)
        worst_dd = min(worst_dd, equity - peak)
    return worst_dd


def report_executed_table(strategy_id, since):
    with SessionLocal() as db:
        rows = db.query(MT5CandidateEvaluationORM).filter(
            MT5CandidateEvaluationORM.strategy == strategy_id,
            MT5CandidateEvaluationORM.created_at >= since,
            MT5CandidateEvaluationORM.outcome_type == "EXECUTED",
        ).order_by(MT5CandidateEvaluationORM.created_at.asc()).all()

    print(f"\n{'='*78}\n{strategy_id} V2 -- EXECUTED trades since {since.isoformat()}\n{'='*78}")
    print(f"total executed: {len(rows)}")
    resolved = [r for r in rows if r_of(r) is not None]
    print(f"resolved (closed): {len(resolved)}")
    if not resolved:
        print("Too early -- no resolved executed trades yet. Re-run this report as trades close.")
        return

    vals = [r_of(r) for r in resolved]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    n = len(vals)
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    pf = round(gw / gl, 3) if gl else None
    dd = max_drawdown(vals)
    avg_winner = st.fmean(wins) if wins else None
    avg_loser = st.fmean(losses) if losses else None
    print(f"N={n} win_rate={len(wins)/n:.3f} expectancy_R={st.fmean(vals):+.4f} PF={pf} "
          f"net_R={sum(vals):+.3f} max_DD_R={dd:+.3f} "
          f"avg_winner={avg_winner} avg_loser={avg_loser}")
    mfes = [r.mfe_r for r in resolved if r.mfe_r is not None]
    maes = [r.mae_r for r in resolved if r.mae_r is not None]
    if mfes:
        print(f"avg_MFE={st.fmean(mfes):.3f}")
    if maes:
        print(f"avg_MAE={st.fmean(maes):.3f}")

    print("\nby confidence band:")
    grouped = defaultdict(list)
    for r in resolved:
        grouped[_confidence_band(r.overall_confidence)].append(r_of(r))
    for band, v in sorted(grouped.items(), key=lambda x: -len(x[1])):
        print(f"  {band:<8} n={len(v):>3} exp={st.fmean(v):+.3f}")

    print("\nby symbol:")
    grouped = defaultdict(list)
    for r in resolved:
        grouped[r.symbol].append(r_of(r))
    for sym, v in sorted(grouped.items(), key=lambda x: -len(x[1])):
        print(f"  {sym:<10} n={len(v):>3} exp={st.fmean(v):+.3f}")

    print("\nby regime (evidence field):")
    grouped = defaultdict(list)
    for r in resolved:
        grouped[r.market_regime].append(r_of(r))
    for reg, v in sorted(grouped.items(), key=lambda x: -len(x[1])):
        print(f"  {str(reg):<16} n={len(v):>3} exp={st.fmean(v):+.3f}")

    print("\nper-trade detail:")
    for r in resolved:
        print(f"  {r.created_at} {r.symbol} {r.direction} conf={r.overall_confidence} band={_confidence_band(r.overall_confidence)} "
              f"regime={r.market_regime} realized_r={r_of(r):+.3f} mfe={r.mfe_r} mae={r.mae_r}")


def report_manager_delta(strategy_id, since):
    print(f"\n--- {strategy_id} V2: STATIC -> MANAGED -> DELTA ---")
    with SessionLocal() as db:
        eval_rows = db.query(MT5CandidateEvaluationORM).filter(
            MT5CandidateEvaluationORM.strategy == strategy_id,
            MT5CandidateEvaluationORM.created_at >= since,
            MT5CandidateEvaluationORM.outcome_type == "EXECUTED",
        ).all()
        pos_rows = db.query(AdaptivePositionStateORM).filter(
            AdaptivePositionStateORM.strategy_id == strategy_id,
            AdaptivePositionStateORM.opened_at >= since - timedelta(hours=1),
            AdaptivePositionStateORM.closed_detected_at.isnot(None),
        ).all()

    if not pos_rows:
        print("No closed managed positions yet -- too early.")
        return

    deltas = []
    for p in pos_rows:
        managed_r = (p.max_achieved_r or 0.0) - (p.current_giveback_r or 0.0)
        # best-effort match to the static/unmanaged outcome by symbol+direction+nearest creation time
        candidate_match = min(
            (r for r in eval_rows if r.symbol == p.symbol and r.direction == p.direction and r_of(r) is not None),
            key=lambda r: abs((r.created_at - p.opened_at).total_seconds()) if p.opened_at and r.created_at else float("inf"),
            default=None,
        )
        static_r = r_of(candidate_match) if candidate_match else None
        delta = (managed_r - static_r) if static_r is not None else None
        deltas.append((p, static_r, managed_r, delta))
        print(f"  {p.symbol} {p.direction} opened={p.opened_at} closed={p.closed_detected_at} "
              f"static_R={static_r} managed_R={managed_r:+.3f} delta={delta if delta is None else f'{delta:+.3f}'} "
              f"classification={p.winner_classification}")

    matched = [(s, m, d) for _, s, m, d in deltas if d is not None]
    if matched:
        avg_static = st.fmean(s for s, m, d in matched)
        avg_managed = st.fmean(m for s, m, d in matched)
        avg_delta = st.fmean(d for s, m, d in matched)
        print(f"\n  AGGREGATE (n={len(matched)} matched): avg_static_R={avg_static:+.3f} avg_managed_R={avg_managed:+.3f} avg_delta={avg_delta:+.3f}")
        if avg_delta < -0.05:
            print("  -> Adaptive Manager is net REDUCING realized R vs static SL/TP on this sample.")
        elif avg_delta > 0.05:
            print("  -> Adaptive Manager is net IMPROVING realized R vs static SL/TP on this sample.")
        else:
            print("  -> Adaptive Manager delta is roughly neutral on this sample.")
    print(f"  (n={len(pos_rows)} closed managed positions total -- interpret directionally only until this reaches real size)")


def report_rejection_funnel(strategy_id, since):
    print(f"\n--- {strategy_id} V2: live rejection funnel (since {since.isoformat()}) ---")
    with SessionLocal() as db:
        rows = db.query(MT5CandidateEvaluationORM).filter(
            MT5CandidateEvaluationORM.strategy == strategy_id,
            MT5CandidateEvaluationORM.created_at >= since,
        ).all()
    total = len(rows)
    print(f"  candidates (raw strategy valid, reached confidence scoring): {total}")
    if total == 0:
        print("  No candidates at all yet -- the STRATEGY itself has not triggered since cutover (market conditions / setup rarity), not a downstream gate.")
        return
    conf_pass = [r for r in rows if (r.overall_confidence or 0) >= CONF_THRESHOLD]
    print(f"  confidence >= {CONF_THRESHOLD}: {len(conf_pass)} ({len(conf_pass)/total:.1%})")
    selected = [r for r in rows if r.selected]
    print(f"  selected (won ranking + portfolio/risk passed): {len(selected)} ({len(selected)/total:.1%})")
    executed = [r for r in rows if r.outcome_type == "EXECUTED"]
    print(f"  executed (broker passed, order filled): {len(executed)} ({len(executed)/total:.1%})")

    reasons = Counter(tuple(sorted(r.rejection_reasons or [])) for r in rows if not r.selected)
    print("  rejection reasons among non-selected candidates:")
    for reason, n in reasons.most_common(10):
        print(f"    {n:>4}x {reason if reason else '(none recorded)'}")

    if len(conf_pass) == 0 and total > 0:
        print("  DIAGNOSIS: bottleneck is CONFIDENCE -- the strategy is triggering but nothing clears the threshold.")
    elif len(selected) < len(conf_pass):
        print("  DIAGNOSIS: some candidates clear confidence but are lost to ranking/portfolio/risk gates -- inspect selected vs conf_pass gap.")
    elif len(executed) < len(selected):
        print("  DIAGNOSIS: candidates are selected but not executed -- broker-stage issue.")
    elif total > 0 and len(executed) == total:
        print("  DIAGNOSIS: every recent candidate executed -- no bottleneck, just low raw candidate volume (market conditions).")


def main():
    now = datetime.now(timezone.utc)
    for strategy_id, since in STRATEGIES.items():
        report_executed_table(strategy_id, since)
        report_manager_delta(strategy_id, since)
        report_rejection_funnel(strategy_id, since)
    print(f"\n(report generated {now.isoformat()})")


if __name__ == "__main__":
    main()

"""bsi backtest report -- Part 10/11 metrics, generalizes run_symbol_backtest.py's
methodology (unchanged) to read back whatever run_bsi_backfill.py has already persisted.

Reuses the EXISTING, unmodified HistoricalPatternFingerprintORM/HistoricalSetupOutcomeORM schema
and outcomes.py's own already-computed fields (mfe_r, mae_r, reached_0_25r..reached_2r, tp_hit,
sl_hit, outcome_r, holding_duration_seconds, immediate_failure) -- no new statistics logic, no
recomputation of outcomes. This script only aggregates already-labeled rows and prints the exact
Part 10 metric table (N, win rate, expectancy R, PF, median R, MFE, MAE, max DD proxy, time in
trade, R-milestone reach rates, TP-before-SL / SL-before-TP counts) broken down by subtype,
symbol, direction, session, and regime.

Usage:
    python run_bsi_report.py --mode subtypes --symbols EURUSD GBPUSD
    python run_bsi_report.py --mode components --symbols EURUSD
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import SessionLocal

_ALL_SUBTYPES = ("bsi_order_flow", "bsi_abc", "bsi_asian", "bsi_new_york", "bsi_under_over", "bsi_0930", "bsi_abcd", "bsi_reactionary", "bsi_ob_liquidity")
from backend.mt5_strategies.families.bsi_engine import COMPONENT_STAGES  # noqa: E402


def load_rows(pseudo_strategy_id: str, symbols: list[str] | None, *, start: datetime | None = None, end: datetime | None = None) -> list[tuple]:
    with SessionLocal() as db:
        q = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(
                HistoricalPatternFingerprintORM.anchor_strategy == pseudo_strategy_id,
                HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
                HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
            )
        )
        if symbols:
            q = q.filter(HistoricalPatternFingerprintORM.canonical_symbol.in_(symbols))
        if start is not None:
            q = q.filter(HistoricalPatternFingerprintORM.entry_time >= start)
        if end is not None:
            q = q.filter(HistoricalPatternFingerprintORM.entry_time < end)
        return q.order_by(HistoricalPatternFingerprintORM.entry_time.asc()).all()


def summarize(rows: list[tuple]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    outcomes_r = [o.outcome_r for _, o in rows if o.outcome_r is not None]
    wins = [r for r in outcomes_r if r > 0]
    losses = [r for r in outcomes_r if r <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / len(outcomes_r) if outcomes_r else None
    expectancy = sum(outcomes_r) / len(outcomes_r) if outcomes_r else None
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else None)
    sorted_r = sorted(outcomes_r)
    median_r = sorted_r[len(sorted_r) // 2] if sorted_r else None

    # Simple sequential-equity max drawdown proxy (R units, chronological order, unit risk per trade)
    # + maximum consecutive-losing-trade streak, computed in the same single chronological pass.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for _, o in rows:
        if o.outcome_r is None:
            continue
        equity += o.outcome_r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if o.outcome_r <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    entry_times = [fp.entry_time for fp, _ in rows if fp.entry_time is not None]
    span_days = (max(entry_times) - min(entry_times)).total_seconds() / 86400 if len(entry_times) >= 2 else None
    trades_per_week = round(n / (span_days / 7), 3) if span_days and span_days > 0 else None

    tp_first = sum(1 for _, o in rows if o.tp_hit)
    sl_first = sum(1 for _, o in rows if o.sl_hit)
    holding = [o.holding_duration_seconds for _, o in rows if o.holding_duration_seconds is not None]
    mfe = [o.mfe_r for _, o in rows if o.mfe_r is not None]
    mae = [o.mae_r for _, o in rows if o.mae_r is not None]

    def _reach_rate(attr: str) -> float | None:
        vals = [getattr(o, attr) for _, o in rows if getattr(o, attr) is not None]
        return sum(1 for v in vals if v) / len(vals) if vals else None

    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "expectancy_r": round(expectancy, 4) if expectancy is not None else None,
        "profit_factor": round(pf, 3) if isinstance(pf, float) and pf != float("inf") else pf,
        "median_r": round(median_r, 4) if median_r is not None else None,
        "gross_positive_r": round(gross_profit, 4),
        "gross_negative_r": round(-gross_loss, 4),
        "mean_mfe_r": round(sum(mfe) / len(mfe), 4) if mfe else None,
        "mean_mae_r": round(sum(mae) / len(mae), 4) if mae else None,
        "max_drawdown_r_units": round(max_dd, 4),
        "max_losing_streak": max_losing_streak,
        "mean_holding_minutes": round(sum(holding) / len(holding) / 60, 1) if holding else None,
        "median_holding_minutes": round(sorted(holding)[len(holding) // 2] / 60, 1) if holding else None,
        "tp_hit_count": tp_first,
        "sl_hit_count": sl_first,
        "tp_before_sl_rate": round(tp_first / n, 4) if n else None,
        "trades_per_week": trades_per_week,
        "span_days": round(span_days, 1) if span_days is not None else None,
        "reached_0_25r": _reach_rate("reached_0_25r"),
        "reached_0_5r": _reach_rate("reached_0_5r"),
        "reached_0_75r": _reach_rate("reached_0_75r"),
        "reached_1r": _reach_rate("reached_1r"),
        "reached_1_5r": _reach_rate("reached_1_5r"),
        "reached_2r": _reach_rate("reached_2r"),
        "immediate_failure_rate": _reach_rate("immediate_failure"),
    }


def breakdown_by(rows: list[tuple], key_fn) -> dict:
    groups: dict[str, list[tuple]] = {}
    for fp, o in rows:
        key = str(key_fn(fp, o))
        groups.setdefault(key, []).append((fp, o))
    return {k: summarize(v) for k, v in groups.items()}


def _month_key(fp, o) -> str:
    t = fp.entry_time
    return f"{t.year:04d}-{t.month:02d}" if t else "unknown"


def _half_key(fp, o, midpoint: datetime) -> str:
    if fp.entry_time is None:
        return "unknown"
    return "FIRST_HALF" if fp.entry_time < midpoint else "SECOND_HALF"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["subtypes", "components"], default="subtypes")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--ids", nargs="*", default=None)
    parser.add_argument("--start", type=str, default=None, help="ISO date -- 6-month PRIMARY window start, e.g. 2026-03-01")
    parser.add_argument("--end", type=str, default=None, help="ISO date -- window end (exclusive), e.g. 2026-08-29")
    parser.add_argument("--recent-months", type=int, default=3, help="width of the recent sub-window reported alongside the full window (Section 8: last 3 months)")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    ids = args.ids or (list(COMPONENT_STAGES) if args.mode == "components" else list(_ALL_SUBTYPES))
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc) if args.start else None
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end else None
    report: dict = {"mode": args.mode, "symbols": args.symbols, "window_start": args.start, "window_end": args.end, "results": {}}

    for pid in ids:
        pseudo = f"bsi__{pid}"
        rows = load_rows(pseudo, args.symbols, start=start, end=end)
        print(f"\n=== {pseudo} ({len(rows)} resolved candidates, window {args.start or 'ALL'}..{args.end or 'ALL'}) ===", flush=True)
        overall = summarize(rows)
        print("OVERALL (6-MONTH FULL WINDOW):", overall, flush=True)

        recent_start = None
        if end is not None:
            recent_start = datetime(end.year - (1 if end.month <= args.recent_months else 0),
                                     ((end.month - args.recent_months - 1) % 12) + 1, 1, tzinfo=timezone.utc)
        recent_rows = [r for r in rows if recent_start is None or r[0].entry_time >= recent_start]
        recent_summary = summarize(recent_rows)
        print(f"RECENT {args.recent_months}-MONTH SUB-WINDOW:", recent_summary, flush=True)

        by_symbol = breakdown_by(rows, lambda fp, o: fp.canonical_symbol)
        by_direction = breakdown_by(rows, lambda fp, o: fp.direction)
        by_session = breakdown_by(rows, lambda fp, o: fp.session or "unknown")
        by_regime = breakdown_by(rows, lambda fp, o: fp.regime_broad or fp.regime or "unknown")
        by_month = breakdown_by(rows, _month_key)
        by_half = {}
        if start is not None and end is not None:
            midpoint = start + (end - start) / 2
            by_half = breakdown_by(rows, lambda fp, o: _half_key(fp, o, midpoint))
        print("BY SYMBOL:", json.dumps(by_symbol, indent=2), flush=True)
        print("BY DIRECTION:", json.dumps(by_direction, indent=2), flush=True)
        print("BY SESSION:", json.dumps(by_session, indent=2), flush=True)
        print("BY REGIME:", json.dumps(by_regime, indent=2), flush=True)
        print("BY MONTH (chronological stability, Section 12):", json.dumps(dict(sorted(by_month.items())), indent=2), flush=True)
        print("FIRST-HALF vs SECOND-HALF (Section 12):", json.dumps(by_half, indent=2), flush=True)
        report["results"][pid] = {
            "overall_6m": overall, f"recent_{args.recent_months}m": recent_summary,
            "by_symbol": by_symbol, "by_direction": by_direction, "by_session": by_session,
            "by_regime": by_regime, "by_month": by_month, "by_half": by_half,
        }

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nWrote {args.out}", flush=True)


if __name__ == "__main__":
    main()

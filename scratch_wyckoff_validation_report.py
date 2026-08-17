"""Wyckoff historical validation report (Step 3/6 of the Wyckoff strategy build).

Loads every wyckoff occurrence persisted by run_wyckoff_backfill.py (real, point-in-time-safe
replay -- see that script's docstring), re-derives each occurrence's setup/phase/schematic
evidence by calling the REAL evaluate_wyckoff again at that exact historical instant (the
persisted fingerprint schema is standardized across all strategies and does not carry a
per-strategy evidence blob -- re-deriving is exact, not an approximation, since evaluate_wyckoff
is a pure function of the same point-in-time-safe candles), and in the SAME pass measures
candidate overlap against liquidity_sweep_reversal/smc_continuation/support_resistance_bounce by
calling the REAL evaluate_all at that same instant (never a custom overlap heuristic).

Reports: n / expectancy R / PF / win rate / avg winner / avg loser / max DD / MFE / MAE / reach
(1R/2R from the schema's own reached_1r/reached_2r flags, 3R as an mfe_r>=3.0 proxy since the
schema caps labeled milestones at 2R -- see outcomes.py::_R_MILESTONES) / OOS retention, broken
down by symbol / direction / setup / phase / regime / session, plus chronological train/OOS +
rolling walk-forward (both a custom report matching this session's own established pattern, and
the platform's own canonical walk_forward.run_walk_forward for its edge-stability verdict), plus a
same-window comparison against the 3 overlap-candidate strategies where they already have
in-window coverage.

Research only -- writes nothing, promotes nothing. Run after run_wyckoff_backfill.py completes.
"""
from __future__ import annotations

import asyncio
import os
import statistics as pystats
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

os.environ.setdefault("MT5_STRATEGY_ACTIVATION_WYCKOFF", "SHADOW_MT5")

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM  # noqa: E402
from backend.historical_intelligence.replay import bars_as_of, required_lookback  # noqa: E402
from backend.historical_intelligence.walk_forward import run_walk_forward  # noqa: E402
from backend.mt5_strategies.context import build_strategy_context  # noqa: E402
from backend.mt5_strategies.families import EVALUATORS, evaluate_all, evaluate_wyckoff  # noqa: E402
from backend.shared.db import SessionLocal  # noqa: E402

WINDOW_START = datetime(2026, 2, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 1, tzinfo=timezone.utc)
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GBPJPY"]
COMPARISON_STRATEGIES = ["liquidity_sweep_reversal", "smc_continuation", "support_resistance_bounce"]
MIN_FOLD_N = 10


def load_wyckoff_rows() -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalPatternFingerprintORM.anchor_strategy == "wyckoff")
            .filter(HistoricalPatternFingerprintORM.entry_time >= WINDOW_START, HistoricalPatternFingerprintORM.entry_time < WINDOW_END)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED")
            .filter(HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
            .all()
        )
    out = []
    for fp, oc in rows:
        out.append({
            "fingerprint_id": fp.fingerprint_id, "symbol": fp.canonical_symbol, "direction": fp.direction,
            "regime": fp.regime, "regime_broad": fp.regime_broad, "session": fp.session, "entry_time": fp.entry_time,
            "outcome_r": oc.outcome_r, "net_outcome_r": oc.net_outcome_r, "mfe_r": oc.mfe_r, "mae_r": oc.mae_r,
            "reached_1r": oc.reached_1r, "reached_1_5r": oc.reached_1_5r, "reached_2r": oc.reached_2r,
            "immediate_failure": oc.immediate_failure,
        })
    return out


def load_comparison_rows(strategy_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalPatternFingerprintORM.anchor_strategy == strategy_id)
            .filter(HistoricalPatternFingerprintORM.canonical_symbol.in_(SYMBOLS))
            .filter(HistoricalPatternFingerprintORM.entry_time >= WINDOW_START, HistoricalPatternFingerprintORM.entry_time < WINDOW_END)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED")
            .filter(HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
            .all()
        )
    return [{"outcome_r": oc.outcome_r, "mfe_r": oc.mfe_r, "entry_time": fp.entry_time} for fp, oc in rows]


async def enrich(rows: list[dict]) -> list[dict]:
    """Re-derives wyckoff-specific evidence (setup/phase/schematic) AND measures same-instant
    overlap against COMPARISON_STRATEGIES, both via real production functions at the exact same
    point-in-time reconstruction -- one bars_as_of + build_strategy_context per row, reused for
    both purposes."""
    for i, row in enumerate(rows):
        try:
            m15 = await bars_as_of(canonical_symbol=row["symbol"], broker_symbol=row["symbol"], timeframe="M15", at=row["entry_time"], count=required_lookback(timeframe="M15"), provider="MT5")
            h1 = await bars_as_of(canonical_symbol=row["symbol"], broker_symbol=row["symbol"], timeframe="H1", at=row["entry_time"], count=required_lookback(timeframe="H1"), provider="MT5")
            h4 = await bars_as_of(canonical_symbol=row["symbol"], broker_symbol=row["symbol"], timeframe="H4", at=row["entry_time"], count=required_lookback(timeframe="H4"), provider="MT5")
            if not m15:
                row["setup"] = row["phase"] = row["schematic"] = None
                row["overlapping_strategies"] = []
                continue
            m15_rows = [c.model_dump(mode="json") for c in m15]
            h1_rows = [c.model_dump(mode="json") for c in h1]
            h4_rows = [c.model_dump(mode="json") for c in h4]
            last_close = m15[-1].close
            ctx = build_strategy_context(
                symbol=row["symbol"], broker_symbol=row["symbol"], m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
                bid=last_close, ask=last_close, spread=Decimal("0"), now=row["entry_time"],
            )
            if ctx is None:
                row["setup"] = row["phase"] = row["schematic"] = None
                row["overlapping_strategies"] = []
                continue
            signal = evaluate_wyckoff(ctx)
            row["setup"] = signal.evidence.get("setup")
            row["phase"] = signal.evidence.get("phase")
            row["schematic"] = signal.evidence.get("schematic")
            row["range_position"] = signal.evidence.get("range_position")

            comparison_signals = evaluate_all(ctx, strategy_ids=COMPARISON_STRATEGIES)
            overlapping = [s.strategy_id for s in comparison_signals if s.valid and s.direction == row["direction"]]
            row["overlapping_strategies"] = overlapping
        except Exception as exc:
            row["setup"] = row["phase"] = row["schematic"] = None
            row["overlapping_strategies"] = []
            row["_enrich_error"] = f"{exc.__class__.__name__}: {exc}"
        if (i + 1) % 50 == 0:
            print(f"  enriched {i+1}/{len(rows)}", flush=True)
    return rows


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "status": "NO_DATA"}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {
        "n": n,
        "expectancy_r": round(pystats.fmean(rs), 4),
        "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
        "avg_winner_r": round(pystats.fmean(wins), 4) if wins else None,
        "avg_loser_r": round(pystats.fmean(losses), 4) if losses else None,
        "max_drawdown": _max_dd(rs),
    }


def _max_dd(rs: list[float]) -> float:
    cum = peak = max_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return round(max_dd, 4)


def _report_group(rows: list[dict]) -> dict:
    rs = [r["outcome_r"] for r in rows if r["outcome_r"] is not None]
    mfe = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
    mae = [r["mae_r"] for r in rows if r["mae_r"] is not None]
    stats = _stats(rs)
    if stats["n"] == 0:
        return stats
    stats["median_mfe_r"] = round(pystats.median(mfe), 4) if mfe else None
    stats["median_mae_r"] = round(pystats.median(mae), 4) if mae else None
    stats["reach_1r"] = round(sum(1 for r in rows if r.get("reached_1r")) / len(rows), 4)
    stats["reach_2r"] = round(sum(1 for r in rows if r.get("reached_2r")) / len(rows), 4)
    stats["reach_3r_proxy_mfe"] = round(sum(1 for r in rows if (r.get("mfe_r") or 0) >= 3.0) / len(rows), 4)
    return stats


def breakdown(rows: list[dict], key: str) -> dict:
    groups: dict[Any, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get(key)].append(r)
    return {str(k): _report_group(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def chronological_walkforward(rows: list[dict]) -> dict:
    rows_sorted = sorted([r for r in rows if r["outcome_r"] is not None], key=lambda r: r["entry_time"])
    rs = [r["outcome_r"] for r in rows_sorted]
    cut = len(rs) // 2
    out = {"all": _stats(rs), "train50": _stats(rs[:cut]), "oos50": _stats(rs[cut:])}
    fold_size = len(rs) // 3
    if fold_size >= MIN_FOLD_N:
        for i in range(3):
            fold = rs[i * fold_size: (i + 1) * fold_size if i < 2 else len(rs)]
            out[f"fold{i + 1}"] = _stats(fold)
    return out


def overlap_summary(rows: list[dict]) -> dict:
    total = len(rows)
    with_overlap = [r for r in rows if r.get("overlapping_strategies")]
    unique = [r for r in rows if not r.get("overlapping_strategies")]
    which = Counter(sid for r in rows for sid in (r.get("overlapping_strategies") or []))
    unique_stats = _report_group(unique)
    overlap_stats = _report_group(with_overlap)
    return {
        "total_wyckoff_occurrences": total,
        "unique_to_wyckoff_count": len(unique),
        "unique_to_wyckoff_pct": round(len(unique) / total, 4) if total else None,
        "overlapping_count": len(with_overlap),
        "overlap_by_strategy": dict(which),
        "unique_setups_stats": unique_stats,
        "overlapping_setups_stats": overlap_stats,
    }


async def main() -> None:
    print(f"Loading wyckoff occurrences {WINDOW_START.date()} -> {WINDOW_END.date()} for {SYMBOLS}...", flush=True)
    rows = load_wyckoff_rows()
    print(f"Loaded {len(rows)} resolved wyckoff occurrences.", flush=True)
    if not rows:
        print("NO WYCKOFF OCCURRENCES FOUND -- nothing to report. Check run_wyckoff_backfill.py completed.")
        return

    print("Enriching with setup/phase/schematic + overlap evidence (re-derived, real evaluate_wyckoff/evaluate_all calls)...", flush=True)
    rows = await enrich(rows)

    print("\n" + "=" * 100)
    print("OVERALL")
    print("=" * 100)
    print(_report_group(rows))

    print("\n" + "=" * 100)
    print("BY SYMBOL")
    print("=" * 100)
    for k, v in breakdown(rows, "symbol").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("BY DIRECTION")
    print("=" * 100)
    for k, v in breakdown(rows, "direction").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("BY SETUP TYPE")
    print("=" * 100)
    for k, v in breakdown(rows, "setup").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("BY WYCKOFF PHASE")
    print("=" * 100)
    for k, v in breakdown(rows, "phase").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("BY REGIME")
    print("=" * 100)
    for k, v in breakdown(rows, "regime").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("BY SESSION")
    print("=" * 100)
    for k, v in breakdown(rows, "session").items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("CHRONOLOGICAL TRAIN/OOS + ROLLING WALK-FORWARD (custom, all symbols pooled)")
    print("=" * 100)
    print(chronological_walkforward(rows))

    for symbol in SYMBOLS:
        symbol_rows = [r for r in rows if r["symbol"] == symbol]
        if symbol_rows:
            print(f"\n  {symbol} walk-forward: {chronological_walkforward(symbol_rows)}")

    print("\n" + "=" * 100)
    print("CANONICAL PLATFORM WALK-FORWARD (walk_forward.run_walk_forward, anchor_strategy='wyckoff')")
    print("=" * 100)
    try:
        result = run_walk_forward(anchor_strategy="wyckoff")
        print(result)
    except Exception as exc:
        print(f"run_walk_forward failed/insufficient: {exc.__class__.__name__}: {exc}")

    print("\n" + "=" * 100)
    print("OVERLAP WITH liquidity_sweep_reversal / smc_continuation / support_resistance_bounce")
    print("=" * 100)
    print(overlap_summary(rows))

    print("\n" + "=" * 100)
    print(f"SAME-WINDOW COMPARISON AGAINST EXISTING STRATEGIES ({WINDOW_START.date()} -> {WINDOW_END.date()}, same {SYMBOLS})")
    print("=" * 100)
    print(f"  wyckoff: {_report_group(rows)}")
    for strategy_id in COMPARISON_STRATEGIES + ["ema_trend", "trend_pullback", "breakout", "mean_reversion", "momentum", "session_breakout", "vwap_reversion"]:
        comp_rows = load_comparison_rows(strategy_id)
        if comp_rows:
            print(f"  {strategy_id}: {_report_group(comp_rows)}")
        else:
            print(f"  {strategy_id}: NO_IN_WINDOW_COVERAGE (not directly comparable without backfilling this exact window)")


if __name__ == "__main__":
    asyncio.run(main())

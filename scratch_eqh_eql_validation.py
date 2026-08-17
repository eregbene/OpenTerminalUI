"""EQH/EQL validation (2026-08-17 audit -> backend/market_structure/liquidity.py::
detect_equal_levels). Mirrors scratch_squeeze_momentum_validation.py's methodology exactly,
including the lesson learned there: pooled (unsplit) correlations are not evidence, only a
chronological OOS split is.

Tests both roles the user asked for:
  (1) live strategy confirmation/filter value for liquidity_sweep_reversal, breakout,
      smc_continuation, support_resistance_bounce
  (2) Historical Intelligence fingerprint/similarity value (pooled across all strategies)

Same scope decision as the squeeze validation: FOREXSB-provider EURUSD only (full 2018-2026
UTC-corrected series, majority provider for this symbol's fingerprints). Same no-lookahead
join: a candidate only ever sees swings/equal-levels confirmed (confirmation_time) strictly
before its entry_time.

Nothing here writes to any table.
"""
from __future__ import annotations

import bisect
import sys
from datetime import timedelta

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM as FP, HistoricalSetupOutcomeORM as OUT
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import get_profile
from backend.market_structure.liquidity import detect_equal_levels, detect_liquidity_sweeps
from backend.market_structure.swings import detect_swings
from backend.shared.db import SessionLocal

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
PROVIDER = sys.argv[2] if len(sys.argv) > 2 else "FOREXSB"
CONFIRM_STRATEGIES = {"liquidity_sweep_reversal", "breakout", "smc_continuation", "support_resistance_bounce"}


def load_swings_and_equal_levels():
    print(f"Loading {PROVIDER} M15 canonical candles for {SYMBOL}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM)
            .filter(
                MT5CanonicalCandleORM.broker_symbol == SYMBOL,
                MT5CanonicalCandleORM.provider == PROVIDER,
                MT5CanonicalCandleORM.timeframe == "M15",
                MT5CanonicalCandleORM.quality != "INVALID",
                MT5CanonicalCandleORM.timestamp_utc.isnot(None),
            )
            .order_by(MT5CanonicalCandleORM.timestamp_utc.asc())
            .all()
        )
    print(f"  {len(rows)} bars loaded", flush=True)
    dict_rows = [
        {"time": r.timestamp_utc, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.tick_volume}
        for r in rows
    ]
    bars = normalize_bars(dict_rows, symbol=SYMBOL, timeframe="M15")
    config = get_profile("balanced")
    print("Detecting swings over the full history (single causal pass)...", flush=True)
    swings = detect_swings(bars, config, symbol=SYMBOL, timeframe="M15")
    print(f"  {len(swings)} swings", flush=True)
    print("Clustering EQH/EQL pools...", flush=True)
    equal_levels = detect_equal_levels(bars, swings, config, symbol=SYMBOL, timeframe="M15")
    print(f"  {len(equal_levels)} equal-level confirmation events", flush=True)
    equal_level_sweeps = detect_liquidity_sweeps(bars, equal_levels, config, symbol=SYMBOL, timeframe="M15")
    print(f"  {len(equal_level_sweeps)} equal-level sweeps", flush=True)
    return bars, equal_levels, equal_level_sweeps


def load_candidates():
    print(f"Loading RESOLVED fingerprints for {SYMBOL}/{PROVIDER}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(
                FP.fingerprint_id, FP.anchor_strategy, FP.direction, FP.entry_time,
                FP.regime_broad, FP.atr_regime, FP.liquidity_sweep_present, FP.liquidity_location,
                OUT.outcome_r, OUT.reached_1r,
            )
            .join(OUT, OUT.fingerprint_id == FP.fingerprint_id)
            .filter(FP.canonical_symbol == SYMBOL, FP.provider == PROVIDER, OUT.resolution_status == "RESOLVED")
            .order_by(FP.entry_time.asc())
            .all()
        )
    print(f"  {len(rows)} resolved candidates loaded", flush=True)
    return rows


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "win_rate": None, "profit_factor": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)
    return {
        "n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
    }


def _split(rows: list) -> tuple[list, list]:
    n = len(rows)
    cut = int(n * 0.6)
    return rows[:cut], rows[cut:]


def _point_in_time_state(events: list, times: list, at):
    """`events` sorted ascending by confirmation_time; `times` is the matching parallel list of
    confirmation_time values (kept separate for bisect). Returns every event confirmed strictly
    before `at`."""
    idx = bisect.bisect_left(times, at)
    return events[:idx]


def main():
    bars, equal_levels, equal_level_sweeps = load_swings_and_equal_levels()
    candidates = load_candidates()

    eqh_levels = sorted([lvl for lvl in equal_levels if lvl.side == "buy_side"], key=lambda l: l.confirmation_time)
    eql_levels = sorted([lvl for lvl in equal_levels if lvl.side == "sell_side"], key=lambda l: l.confirmation_time)
    eqh_times = [l.confirmation_time for l in eqh_levels]
    eql_times = [l.confirmation_time for l in eql_levels]

    eqh_sweeps = sorted([s for s in equal_level_sweeps if s.side == "buy_side"], key=lambda s: s.confirmation_time)
    eql_sweeps = sorted([s for s in equal_level_sweeps if s.side == "sell_side"], key=lambda s: s.confirmation_time)
    eqh_sweep_times = [s.confirmation_time for s in eqh_sweeps]
    eql_sweep_times = [s.confirmation_time for s in eql_sweeps]

    RECENT_WINDOW = timedelta(hours=1, minutes=30)  # ~6 M15 bars, matches summarize_smc_evidence's recent_window convention
    # Same ~100-bar/25h horizon detect_equal_levels itself uses to bound cluster membership
    # (liquidity.py::detect_equal_levels' _LOOKBACK_BARS) -- an EQH/EQL pool from years earlier
    # is not "active" liquidity a fresh candidate could plausibly interact with.
    STALE_AFTER = timedelta(hours=25)

    def _level_id(lvl):
        return lvl.id

    joined = []
    for row in candidates:
        at = row.entry_time
        recent_eqh_sweeps = [s for s in _point_in_time_state(eqh_sweeps, eqh_sweep_times, at) if at - s.confirmation_time <= RECENT_WINDOW]
        recent_eql_sweeps = [s for s in _point_in_time_state(eql_sweeps, eql_sweep_times, at) if at - s.confirmation_time <= RECENT_WINDOW]
        swept_eqh_ids_before_at = {s.level_id for s in _point_in_time_state(eqh_sweeps, eqh_sweep_times, at)}
        swept_eql_ids_before_at = {s.level_id for s in _point_in_time_state(eql_sweeps, eql_sweep_times, at)}
        known_eqh = _point_in_time_state(eqh_levels, eqh_times, at)
        known_eql = _point_in_time_state(eql_levels, eql_times, at)
        eqh_active = any(_level_id(lvl) not in swept_eqh_ids_before_at and at - lvl.confirmation_time <= STALE_AFTER for lvl in known_eqh)
        eql_active = any(_level_id(lvl) not in swept_eql_ids_before_at and at - lvl.confirmation_time <= STALE_AFTER for lvl in known_eql)
        joined.append((row, eqh_active, eql_active, bool(recent_eqh_sweeps), bool(recent_eql_sweeps)))

    print("\n" + "=" * 100)
    print("ROLE 2: Historical Intelligence fingerprint value (all strategies pooled, chronological 60/40 split)")
    print("=" * 100)
    joined_sorted = sorted(joined, key=lambda t: t[0].entry_time)
    for label, predicate in [
        ("eqh_active (any unswept EQH pool present)", lambda t: t[1]),
        ("eql_active (any unswept EQL pool present)", lambda t: t[2]),
        ("recent eqh sweep (last ~90min)", lambda t: t[3]),
        ("recent eql sweep (last ~90min)", lambda t: t[4]),
        ("neither eqh nor eql active (baseline)", lambda t: not t[1] and not t[2]),
    ]:
        subset = [t for t in joined_sorted if predicate(t)]
        train, oos = _split(subset)
        train_rs = [t[0].outcome_r for t in train if t[0].outcome_r is not None]
        oos_rs = [t[0].outcome_r for t in oos if t[0].outcome_r is not None]
        all_rs = [t[0].outcome_r for t in subset if t[0].outcome_r is not None]
        print(f"  {label:45s} all={_stats(all_rs)}")
        print(f"  {'':45s} train60={_stats(train_rs)}  oos40={_stats(oos_rs)}")

    print("\n" + "=" * 100)
    print("ROLE 1: confirmation-filter value for liquidity_sweep_reversal/breakout/smc_continuation/support_resistance_bounce")
    print("=" * 100)
    for strat in sorted(CONFIRM_STRATEGIES):
        strat_rows = sorted([t for t in joined if t[0].anchor_strategy == strat], key=lambda t: t[0].entry_time)
        if not strat_rows:
            continue
        print(f"\n--- {strat} (n={len(strat_rows)} total resolved candidates) ---")

        def direction_matches_eqh_eql(row, eqh_active, eql_active, eqh_swept, eql_swept):
            # A short trade is confirmed by resistance-side liquidity (EQH: sold into/rejected
            # from a repeated-high pool, or a recent EQH sweep-and-reject); a long trade by the
            # mirror EQL condition.
            long = row.direction.upper() == "LONG"
            if long:
                return eql_active or eql_swept
            return eqh_active or eqh_swept

        for label, predicate in [
            ("baseline (no filter)", lambda t: True),
            ("EQH/EQL-side liquidity present/recently swept (direction-matched)", lambda t: direction_matches_eqh_eql(*t)),
        ]:
            filtered = [t for t in strat_rows if predicate(t)]
            train, oos = _split(filtered)
            train_rs = [t[0].outcome_r for t in train if t[0].outcome_r is not None]
            oos_rs = [t[0].outcome_r for t in oos if t[0].outcome_r is not None]
            all_rs = [t[0].outcome_r for t in filtered if t[0].outcome_r is not None]
            print(f"  {label:70s} all={_stats(all_rs)}")
            print(f"  {'':70s} train60={_stats(train_rs)}  oos40={_stats(oos_rs)}")


if __name__ == "__main__":
    main()

"""LuxAlgo-derived concepts validation: pivot-confidence + volume-delta divergence, independently
implemented (market_structure/pivot_confidence.py, market_structure/volume_delta.py) from public
methodology only -- see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md.

Both concepts are naturally CONFIRMATION signals, not standalone entry generators (pivot-
confidence scores an EXISTING pivot; volume-delta divergence compares against an EXISTING price
extreme) -- scoped to Role B (confirmation-filter for relevant existing strategies), mirroring
the squeeze/EQH-EQL validation methodology and its hard-won lesson: chronological 50/50 split
required, pooled-only results are not evidence.

Relevant strategies chosen by what each concept logically informs:
  - pivot_confidence: support_resistance_bounce, liquidity_sweep_reversal, breakout (all three
    depend on a swing/level's reliability).
  - volume_delta divergence: momentum, mean_reversion, smc_continuation, liquidity_sweep_reversal
    (all four are directional/reversal calls where confirming or contradicting volume pressure is
    logically relevant).

Nothing here writes to any table.
"""
from __future__ import annotations

import bisect
from collections import Counter

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM as FP, HistoricalSetupOutcomeORM as OUT
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import get_profile
from backend.market_structure.pivot_confidence import compute_signatures, pivot_confidence_series
from backend.market_structure.swings import detect_swings
from backend.market_structure.volume_delta import cumulative_delta, divergence_states
from backend.shared.db import SessionLocal

SYMBOL = "EURUSD"
PROVIDER = "FOREXSB"
PIVOT_STRATEGIES = {"support_resistance_bounce", "liquidity_sweep_reversal", "breakout"}
VOLUME_STRATEGIES = {"momentum", "mean_reversion", "smc_continuation", "liquidity_sweep_reversal"}


def load_bars():
    print(f"Loading {PROVIDER} M15 canonical candles for {SYMBOL}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM)
            .filter(
                MT5CanonicalCandleORM.broker_symbol == SYMBOL, MT5CanonicalCandleORM.provider == PROVIDER,
                MT5CanonicalCandleORM.timeframe == "M15", MT5CanonicalCandleORM.quality != "INVALID",
                MT5CanonicalCandleORM.timestamp_utc.isnot(None),
            )
            .order_by(MT5CanonicalCandleORM.timestamp_utc.asc())
            .all()
        )
    print(f"  {len(rows)} bars loaded", flush=True)
    dict_rows = [{"time": r.timestamp_utc, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.tick_volume} for r in rows]
    bars = normalize_bars(dict_rows, symbol=SYMBOL, timeframe="M15")
    return bars


def build_pivot_confidence_by_time(bars):
    print("Computing swings + pivot signatures + confidence scores...", flush=True)
    config = get_profile("balanced")
    swings = detect_swings(bars, config, symbol=SYMBOL, timeframe="M15")
    signatures = compute_signatures(bars, swings)
    confidence = pivot_confidence_series(signatures)
    by_swing_id = {s.id: s for s in swings}
    # (confirmation_time, swing_type, confidence) sorted -- point-in-time join key
    events = []
    for sig in signatures:
        conf = confidence.get(sig.swing_id)
        swing = by_swing_id[sig.swing_id]
        events.append((swing.confirmation_time, sig.swing_type, conf))
    events.sort(key=lambda t: t[0])
    print(f"  {len(events)} scored pivots ({sum(1 for e in events if e[2] is not None)} with a usable confidence score)", flush=True)
    return events


def build_volume_divergence_by_time(bars):
    print("Computing cumulative delta + divergence states...", flush=True)
    cvd = cumulative_delta(bars)
    states = divergence_states(bars, cvd)
    close_times = [b.close_time for b in bars]
    return close_times, states


def point_in_time_latest_confidence(events, swing_type, at):
    """Latest CONFIRMED pivot of `swing_type` with confirmation_time <= at, or None."""
    same_type = [e for e in events if e[1] == swing_type and e[0] <= at]
    if not same_type:
        return None
    return same_type[-1][2]


def load_candidates():
    print(f"Loading RESOLVED fingerprints for {SYMBOL}/{PROVIDER}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(FP.fingerprint_id, FP.anchor_strategy, FP.direction, FP.entry_time, OUT.outcome_r, OUT.reached_1r)
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
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {"n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4), "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf}


def _split_half(items):
    cut = len(items) // 2
    return items[:cut], items[cut:]


def main():
    bars = load_bars()
    pivot_events = build_pivot_confidence_by_time(bars)
    close_times, div_states = build_volume_divergence_by_time(bars)
    candidates = load_candidates()

    print("\n" + "=" * 100)
    print("PIVOT-CONFIDENCE: confirmation-filter value (support_resistance_bounce, liquidity_sweep_reversal, breakout)")
    print("=" * 100)
    for strat in sorted(PIVOT_STRATEGIES):
        strat_rows = sorted([r for r in candidates if r.anchor_strategy == strat], key=lambda r: r.entry_time)
        if not strat_rows:
            continue
        print(f"\n--- {strat} (n={len(strat_rows)} total resolved candidates) ---")
        # Direction-aware: a LONG (buying support/reversal-up) is confirmed by a HIGH-confidence
        # recent LOW pivot (the level it's bouncing/reversing off); a SHORT by a high-confidence HIGH pivot.
        joined = []
        for r in strat_rows:
            swing_type = "low" if r.direction.upper() == "LONG" else "high"
            conf = point_in_time_latest_confidence(pivot_events, swing_type, r.entry_time)
            joined.append((r, conf))
        for label, predicate in [
            ("baseline (no filter)", lambda t: True),
            ("high pivot-confidence (>=0.7)", lambda t: t[1] is not None and t[1] >= 0.7),
            ("low pivot-confidence (<0.3)", lambda t: t[1] is not None and t[1] < 0.3),
        ]:
            filtered = [t for t in joined if predicate(t)]
            first_half, second_half = _split_half(filtered)
            all_rs = [t[0].outcome_r for t in filtered if t[0].outcome_r is not None]
            h1 = [t[0].outcome_r for t in first_half if t[0].outcome_r is not None]
            h2 = [t[0].outcome_r for t in second_half if t[0].outcome_r is not None]
            print(f"  {label:32s} all={_stats(all_rs)}")
            print(f"  {'':32s} 1st-half={_stats(h1)}  2nd-half={_stats(h2)}")

    print("\n" + "=" * 100)
    print("VOLUME-DELTA DIVERGENCE: confirmation-filter value (momentum, mean_reversion, smc_continuation, liquidity_sweep_reversal)")
    print("=" * 100)
    for strat in sorted(VOLUME_STRATEGIES):
        strat_rows = sorted([r for r in candidates if r.anchor_strategy == strat], key=lambda r: r.entry_time)
        if not strat_rows:
            continue
        print(f"\n--- {strat} (n={len(strat_rows)} total resolved candidates) ---")
        joined = []
        for r in strat_rows:
            idx = bisect.bisect_right(close_times, r.entry_time) - 1
            state = div_states[idx] if 0 <= idx < len(div_states) else None
            joined.append((r, state))
        for label, predicate in [
            ("baseline (no filter)", lambda t: True),
            ("aligned divergence (bullish div + LONG, or bearish div + SHORT)",
             lambda t: t[1] is not None and ((t[1].bullish and t[0].direction.upper() == "LONG") or (t[1].bearish and t[0].direction.upper() == "SHORT"))),
            ("contradicting divergence (opposite)",
             lambda t: t[1] is not None and ((t[1].bearish and t[0].direction.upper() == "LONG") or (t[1].bullish and t[0].direction.upper() == "SHORT"))),
        ]:
            filtered = [t for t in joined if predicate(t)]
            first_half, second_half = _split_half(filtered)
            all_rs = [t[0].outcome_r for t in filtered if t[0].outcome_r is not None]
            h1 = [t[0].outcome_r for t in first_half if t[0].outcome_r is not None]
            h2 = [t[0].outcome_r for t in second_half if t[0].outcome_r is not None]
            print(f"  {label:60s} all={_stats(all_rs)}")
            print(f"  {'':60s} 1st-half={_stats(h1)}  2nd-half={_stats(h2)}")


if __name__ == "__main__":
    main()

"""Stage 2 of the LazyBear Squeeze Momentum validation (docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md,
commit "[Stage 1] LazyBear squeeze/momentum shared feature"). Tests all four roles the user asked
for from ONE point-in-time-safe backfill of the shared feature over the real historical corpus:

  (A) standalone hypothetical squeeze-release+momentum-direction entry strategy
  (B) confirmation-filter value for breakout/momentum/session_breakout/smc_continuation
  (C) Historical Intelligence fingerprint/similarity value (redundancy + incremental signal)
  (D) combined (B-confirmed candidates further split by whether (C) would have supported them)

Scope decision (stated, not hidden): computed only against the FOREXSB-provider EURUSD canonical
candle series. FOREXSB has full 2018-2026 UTC-corrected timestamps (timestamp_utc fully populated)
and is the majority provider for EURUSD fingerprints (124,561 of 215,714 RESOLVED-outcome rows).
The MT5-provider series' timestamp_utc is not populated for this symbol (broker-server-time
correction gap, separate from anything this validation touches) -- mixing it in without resolving
that would silently blend a differently-timed series into the same squeeze computation, so it is
left out of this pass rather than guessed at.

No lookahead: bars_close_time = timestamp_utc + 15min is precisely when a bar's OHLC (and
everything computed from bars up to and including it) first becomes knowable. Every join below
looks up the squeeze state from the LATEST bar whose close_time <= the candidate's entry_time --
never a bar that closes at or after entry_time.

Nothing here writes to any table. Read-only against mt5_canonical_candles / historical_pattern_
fingerprints / historical_setup_outcomes.
"""
from __future__ import annotations

import bisect
import sys
from datetime import timedelta
from decimal import Decimal

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM as FP, HistoricalSetupOutcomeORM as OUT
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.squeeze_momentum import (
    SQUEEZE_OFF, SQUEEZE_ON, SQUEEZE_RELEASING,
    bollinger_bands, keltner_channels, squeeze_momentum, squeeze_states,
)
from backend.shared.db import SessionLocal

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
PROVIDER = sys.argv[2] if len(sys.argv) > 2 else "FOREXSB"
CONFIRM_STRATEGIES = {"breakout", "momentum", "session_breakout", "smc_continuation"}

# Standalone role-A stop/target convention: matches trend_pullback's exact ATR multiples
# (families.py:250,253) -- a representative, not cherry-picked, existing-strategy convention
# (median across the 11 strategies is stop~1.0-1.5xATR, target~2.5-3.0xATR).
_STOP_ATR_MULT = Decimal("1.2")
_TARGET_ATR_MULT = Decimal("2.5")
_MAX_LOOKFORWARD_BARS = 800  # mirrors outcomes.py._MAX_LOOKFORWARD_BARS


def load_bar_series():
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
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    momentum = squeeze_momentum(bars)
    atrs = average_true_range(bars, 14)
    close_times = [b.close_time for b in bars]
    return bars, states, momentum, atrs, close_times


def point_in_time_index(close_times: list, at) -> int | None:
    """Index of the LATEST bar whose close_time <= at, or None if none exists yet."""
    i = bisect.bisect_right(close_times, at) - 1
    return i if i >= 0 else None


def load_candidates():
    print(f"Loading RESOLVED fingerprints for {SYMBOL}/{PROVIDER}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(
                FP.fingerprint_id, FP.anchor_strategy, FP.direction, FP.entry_time,
                FP.regime_broad, FP.atr_regime, FP.session,
                OUT.outcome_r, OUT.reached_1r, OUT.tp_hit, OUT.sl_hit,
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
        "n": n,
        "expectancy_r": round(sum(rs) / n, 4),
        "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
    }


def _split(rows: list) -> tuple[list, list]:
    n = len(rows)
    cut = int(n * 0.6)
    return rows[:cut], rows[cut:]


def role_bc(candidates, close_times, states, momentum):
    print("\n" + "=" * 100)
    print("ROLE C: Historical Intelligence fingerprint/similarity value (all strategies combined)")
    print("=" * 100)

    joined = []
    unmatched = 0
    for row in candidates:
        idx = point_in_time_index(close_times, row.entry_time)
        if idx is None:
            unmatched += 1
            continue
        joined.append((row, states[idx], momentum[idx]))
    print(f"Joined {len(joined)} candidates to point-in-time squeeze state ({unmatched} too early for any closed bar)")

    # --- redundancy check: is squeeze_state just a relabeling of atr_regime / regime_broad? ---
    from collections import Counter
    ctab_atr = Counter((s, row.atr_regime) for row, s, m in joined if s is not None)
    ctab_regime = Counter((s, row.regime_broad) for row, s, m in joined if s is not None)
    print("\nsqueeze_state x atr_regime contingency (redundancy check):")
    for state in (SQUEEZE_ON, SQUEEZE_RELEASING, SQUEEZE_OFF):
        total = sum(v for k, v in ctab_atr.items() if k[0] == state)
        if total == 0:
            continue
        dist = {k[1]: round(v / total, 3) for k, v in ctab_atr.items() if k[0] == state}
        print(f"  {state:10s} n={total:7d}  atr_regime dist={dist}")
    print("squeeze_state x regime_broad contingency (redundancy check):")
    for state in (SQUEEZE_ON, SQUEEZE_RELEASING, SQUEEZE_OFF):
        total = sum(v for k, v in ctab_regime.items() if k[0] == state)
        if total == 0:
            continue
        dist = {k[1]: round(v / total, 3) for k, v in ctab_regime.items() if k[0] == state}
        print(f"  {state:10s} n={total:7d}  regime_broad dist={dist}")

    # --- incremental value: does squeeze_state / momentum predict outcome_r? ---
    print("\noutcome_r / reached_1r by squeeze_state (all strategies, all candidates):")
    for state in (SQUEEZE_ON, SQUEEZE_RELEASING, SQUEEZE_OFF, None):
        subset = [row for row, s, m in joined if s == state]
        rs = [row.outcome_r for row in subset if row.outcome_r is not None]
        r1 = [row.reached_1r for row in subset if row.reached_1r is not None]
        if not subset:
            continue
        label = state or "NO_SQUEEZE_DATA(warmup)"
        stats = _stats(rs)
        r1_rate = round(sum(1 for x in r1 if x) / len(r1), 4) if r1 else None
        print(f"  {label:22s} n={stats['n']:7d}  expectancy_r={stats['expectancy_r']}  win_rate={stats['win_rate']}  PF={stats['profit_factor']}  reached_1r_rate={r1_rate}")

    # momentum sign vs trade direction alignment, continuous-value correlation
    aligned_rs, opposed_rs, neutral_rs = [], [], []
    mom_vals, r_vals = [], []
    for row, s, m in joined:
        if m is None or row.outcome_r is None:
            continue
        mom_vals.append(m)
        r_vals.append(row.outcome_r)
        long = row.direction.upper() == "LONG"
        if (m > 0) == long and abs(m) > 1e-9:
            aligned_rs.append(row.outcome_r)
        elif (m > 0) != long and abs(m) > 1e-9:
            opposed_rs.append(row.outcome_r)
        else:
            neutral_rs.append(row.outcome_r)
    print("\nmomentum-direction alignment vs trade direction (all strategies):")
    print(f"  aligned (momentum agrees with trade direction): {_stats(aligned_rs)}")
    print(f"  opposed (momentum disagrees):                    {_stats(opposed_rs)}")
    print(f"  neutral (momentum ~0):                           {_stats(neutral_rs)}")

    if len(mom_vals) > 30:
        n = len(mom_vals)
        mean_m = sum(mom_vals) / n
        mean_r = sum(r_vals) / n
        cov = sum((mv - mean_m) * (rv - mean_r) for mv, rv in zip(mom_vals, r_vals))
        var_m = sum((mv - mean_m) ** 2 for mv in mom_vals)
        var_r = sum((rv - mean_r) ** 2 for rv in r_vals)
        pearson_r = cov / ((var_m * var_r) ** 0.5) if var_m > 0 and var_r > 0 else None
        print(f"\nPearson correlation(momentum_value, outcome_r), n={n}: {round(pearson_r, 4) if pearson_r is not None else None}")

    return joined


def role_b_confirmation(joined):
    print("\n" + "=" * 100)
    print("ROLE B: confirmation-filter value for breakout/momentum/session_breakout/smc_continuation")
    print("=" * 100)
    for strat in sorted(CONFIRM_STRATEGIES):
        strat_rows = [(row, s, m) for row, s, m in joined if row.anchor_strategy == strat]
        if not strat_rows:
            continue
        print(f"\n--- {strat} (n={len(strat_rows)} total resolved candidates) ---")
        strat_rows_sorted = sorted(strat_rows, key=lambda t: t[0].entry_time)
        for label, predicate in [
            ("baseline (no filter)", lambda row, s, m: True),
            ("RELEASING + momentum-aligned", lambda row, s, m: s == SQUEEZE_RELEASING and m is not None and ((m > 0) == (row.direction.upper() == "LONG"))),
            ("OFF + momentum-aligned (post-breakout continuation)", lambda row, s, m: s == SQUEEZE_OFF and m is not None and ((m > 0) == (row.direction.upper() == "LONG"))),
            ("momentum-aligned only (any squeeze state)", lambda row, s, m: m is not None and ((m > 0) == (row.direction.upper() == "LONG"))),
        ]:
            filtered = [t for t in strat_rows_sorted if predicate(*t)]
            train, oos = _split(filtered)
            train_rs = [row.outcome_r for row, s, m in train if row.outcome_r is not None]
            oos_rs = [row.outcome_r for row, s, m in oos if row.outcome_r is not None]
            all_rs = [row.outcome_r for row, s, m in filtered if row.outcome_r is not None]
            print(f"  {label:52s} all={_stats(all_rs)}  train60={_stats(train_rs)}  oos40={_stats(oos_rs)}")


def role_a_standalone(bars, states, momentum, atrs, close_times):
    print("\n" + "=" * 100)
    print("ROLE A: standalone squeeze-release + momentum-direction entry strategy")
    print("=" * 100)
    n = len(bars)
    trades = []
    i = 39  # first index where both states and momentum are populated
    while i < n - 1:
        state, mom, atr = states[i], momentum[i], atrs[i]
        if state == SQUEEZE_RELEASING and mom is not None and atr is not None and atr > 0:
            direction = "LONG" if mom > 0 else "SHORT" if mom < 0 else None
            if direction is not None:
                entry_bar = bars[i + 1]  # next bar's open -- realistic execution, not same-bar close
                entry = entry_bar.open
                atr_d = Decimal(str(atr))
                if direction == "LONG":
                    stop = entry - atr_d * _STOP_ATR_MULT
                    target = entry + atr_d * _TARGET_ATR_MULT
                else:
                    stop = entry + atr_d * _STOP_ATR_MULT
                    target = entry - atr_d * _TARGET_ATR_MULT
                risk = abs(entry - stop)
                if risk > 0:
                    outcome_r, resolved = _walk_forward(bars, i + 1, direction, entry, stop, target, risk)
                    if resolved:
                        trades.append({"entry_time": entry_bar.open_time, "direction": direction, "outcome_r": outcome_r})
        i += 1

    print(f"Total standalone trigger events with a resolved outcome: {len(trades)}")
    if not trades:
        return
    trades.sort(key=lambda t: t["entry_time"])
    all_rs = [t["outcome_r"] for t in trades]
    train, oos = _split(trades)
    train_rs = [t["outcome_r"] for t in train]
    oos_rs = [t["outcome_r"] for t in oos]
    print(f"  all:     {_stats(all_rs)}")
    print(f"  train60: {_stats(train_rs)}")
    print(f"  oos40:   {_stats(oos_rs)}")
    long_rs = [t["outcome_r"] for t in trades if t["direction"] == "LONG"]
    short_rs = [t["outcome_r"] for t in trades if t["direction"] == "SHORT"]
    print(f"  LONG only:  {_stats(long_rs)}")
    print(f"  SHORT only: {_stats(short_rs)}")


def _walk_forward(bars, start_idx, direction, entry, stop, target, risk) -> tuple[float | None, bool]:
    """In-memory mirror of outcomes.py::label_outcome's walk-forward core (same SL-touched-first
    tie-break, same R formula, same _MAX_LOOKFORWARD_BARS cap, same mark-to-market-if-unresolved
    convention) -- deliberately not calling label_outcome() itself so this stays a pure in-memory
    read against the bar series already loaded, with zero DB writes for a hypothetical hourly
    stream of synthetic hypothetical trades that were never real candidates."""
    long = direction == "LONG"
    end_idx = min(len(bars), start_idx + _MAX_LOOKFORWARD_BARS)
    for j in range(start_idx, end_idx):
        b = bars[j]
        sl_touched = (b.low <= stop) if long else (b.high >= stop)
        tp_touched = (b.high >= target) if long else (b.low <= target)
        if sl_touched or tp_touched:
            if sl_touched:
                return -1.0, True
            planned_rr = float(abs(target - entry) / risk)
            return planned_rr, True
    if end_idx > start_idx and end_idx - start_idx >= _MAX_LOOKFORWARD_BARS:
        last_close = bars[end_idx - 1].close
        mtm_r = float((last_close - entry) / risk) * (1.0 if long else -1.0)
        return mtm_r, True
    return None, False  # PENDING -- ran off the end of loaded history, not a real resolution


def main():
    bars, states, momentum, atrs, close_times = load_bar_series()
    candidates = load_candidates()
    joined = role_bc(candidates, close_times, states, momentum)
    role_b_confirmation(joined)
    role_a_standalone(bars, states, momentum, atrs, close_times)


if __name__ == "__main__":
    main()

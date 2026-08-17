"""Combined Adaptive Manager walk-forward validation: the two best-supported changes from the
deep audit, tested individually and together, on the exact same real closed DEMO trades.

  B: THESIS_INVALIDATION_CLOSE requires a genuine CHoCH/MSS break against the trade direction
     (reuses the existing point-in-time-safe market_structure.engine.analyze_bars -- no new
     indicator), instead of the bare "2 opposing candles + already -0.25R" rule.
  C: breakeven trigger moved to +0.5R. Implemented via the REAL production knob
     (ADAPTIVE_BREAKEVEN_R env var, read fresh every _evaluate_position call by _env_float) --
     not a separate simulation, the actual decision function with one parameter changed.
  D: both B and C together.
  A: current manager, completely unchanged (baseline for all comparisons).

MFE_PROTECTION_CLOSE is untouched in every policy (per instruction). Hard-safety actions
(THESIS_INVALIDATION_CLOSE's OWN structural gate aside, which is the one being tested)
-- EVENT_RISK_REDUCTION/ECONOMIC_REDUCE_SIZE/TP_PROGRESS_PROFIT_LOCK -- are untouched; this
replay's existing scope limitation (no historical economic-calendar context) means those simply
don't fire here, same as every other script in this audit series.

Reuses scratch_adaptive_manager_forensic_audit.py (base) and
scratch_adaptive_manager_mfe_invalidation_deep_audit.py (make_structural_filter) verbatim.

Nothing here writes to any table or changes production code/behavior.
"""
from __future__ import annotations

import os
import statistics as pystats
from collections import Counter, defaultdict

import scratch_adaptive_manager_forensic_audit as base
import scratch_adaptive_manager_mfe_invalidation_deep_audit as deep
from backend.shared.db import SessionLocal

svc = base.svc
MIN_FOLD_N = 15


def reconstruct_policy(trade, candles, svc_instance, db, *, structural_filter=None) -> dict | None:
    """Full rich forensic reconstruction (mirrors base.reconstruct_forensics exactly), with an
    optional per-cycle structural_filter hook for the CHoCH/MSS-gated invalidation test. The
    breakeven-trigger change needs NO code path here -- it's driven entirely by the
    ADAPTIVE_BREAKEVEN_R env var the caller sets before invoking this function, read fresh by the
    real _evaluate_position -> tp_protection call chain every cycle."""
    normalized = base._normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    state = base._new_state(trade)
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    long = state.direction == "LONG"

    mfe_r, mae_r = 0.0, 0.0
    time_to_milestone = {m: None for m in base.R_MILESTONES}
    for bar in normalized:
        bar_high_r = base._signed_r(state.entry_price, bar["high"] if long else bar["low"], risk, state.direction)
        bar_low_r = base._signed_r(state.entry_price, bar["low"] if long else bar["high"], risk, state.direction)
        mfe_r = max(mfe_r, bar_high_r)
        mae_r = min(mae_r, bar_low_r)
        for m in base.R_MILESTONES:
            if time_to_milestone[m] is None and mfe_r >= m:
                time_to_milestone[m] = (bar["time"] - trade["entry_time"]).total_seconds()

    legs: list[dict] = []
    action_log: list[dict] = []
    final_r, hard_stop_hit, natural_end = 0.0, False, True
    entry_regime = None

    for i, bar in enumerate(normalized):
        window = normalized[max(0, i - base.REGIME_LOOKBACK_BARS):i + 1]
        sl_touched = (bar["low"] <= state.current_sl) if long else (bar["high"] >= state.current_sl)
        tp_touched = (bar["high"] >= state.current_tp) if long else (bar["low"] <= state.current_tp)
        if sl_touched or tp_touched:
            touch_price = state.current_sl if sl_touched else state.current_tp
            final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=touch_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
            hard_stop_hit, natural_end = True, False
            action_log.append({"time": bar["time"], "action": "SL_HIT" if sl_touched else "TP_HIT", "r": final_r})
            break

        r_now, regime_info, atr, _ = base._update_state_for_cycle(state, current_price=bar["close"], candles_window=window)
        if entry_regime is None and str(regime_info.get("regime") or "insufficient_data") != "insufficient_data":
            entry_regime = str(regime_info.get("regime"))
        payload = {"price_current": bar["close"], "price_open": state.entry_price, "sl": state.current_sl, "tp": state.current_tp, "volume": state.current_volume, "profit": None, "symbol": state.symbol}
        candidates = svc_instance._evaluate_position(db, state, payload, {}, window, economic_result=None, symbol_info=None, account_equity=None)
        if structural_filter is not None:
            candidates = structural_filter(bar, window, candidates, state)
        if not base._replay_cooldown_elapsed(state.last_management_at, bar["time"]):
            candidates = [c for c in candidates if c.action_type in ("HOLD", "HOLD_WITH_GIVEBACK_RISK")] or candidates
        choice = svc_instance._select_action(candidates)
        if choice.action_type != "HOLD":
            action_log.append({"time": bar["time"], "action": choice.action_type, "r": r_now})
        closed = base._apply_choice(state, choice, bar_time=bar["time"], current_r=r_now, db=db, legs=legs)
        final_r = r_now
        if closed:
            natural_end = False
            break

    if natural_end and normalized:
        final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=normalized[-1]["close"], original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)

    remaining_fraction = max(0.0, 1.0 - sum(leg["fraction"] for leg in legs))
    if remaining_fraction > 1e-9:
        legs.append({"fraction": remaining_fraction, "r": final_r, "action": "SL_HIT" if hard_stop_hit else ("NATURAL_END" if natural_end else "MANAGER_FULL_CLOSE")})
    blended_r = sum(leg["fraction"] * leg["r"] for leg in legs)
    final_leg = legs[-1] if legs else None

    return {
        "trade_id": trade["trade_id"], "symbol": trade["symbol"], "strategy_id": trade["strategy_id"], "entry_time": trade["entry_time"],
        "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4), "realized_r": round(blended_r, 4), "giveback_r": round(max(0.0, mfe_r - blended_r), 4),
        "action_log": action_log, "final_action": final_leg["action"] if final_leg else "UNKNOWN", "hard_stop_hit": hard_stop_hit,
        "entry_regime": entry_regime or "insufficient_data",
    }


async def run_policy(trades, *, be_trigger: float | None, use_choch_mss: bool) -> list[dict]:
    svc_instance = svc.AdaptiveManagementService()
    db = SessionLocal()
    original_be = os.environ.get("ADAPTIVE_BREAKEVEN_R")
    if be_trigger is not None:
        os.environ["ADAPTIVE_BREAKEVEN_R"] = str(be_trigger)
    elif "ADAPTIVE_BREAKEVEN_R" in os.environ:
        del os.environ["ADAPTIVE_BREAKEVEN_R"]
    results = []
    try:
        for trade in trades:
            try:
                candles = await base.fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                sf = deep.make_structural_filter("choch_mss", trade["symbol"]) if use_choch_mss else None
                r = reconstruct_policy(trade, candles, svc_instance, db, structural_filter=sf)
                if r is not None:
                    results.append(r)
            except Exception as exc:
                print(f"  skipped {trade['trade_id']} ({trade['symbol']}): {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        db.rollback()
        db.close()
        if original_be is not None:
            os.environ["ADAPTIVE_BREAKEVEN_R"] = original_be
        elif "ADAPTIVE_BREAKEVEN_R" in os.environ:
            del os.environ["ADAPTIVE_BREAKEVEN_R"]
    return results


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n < MIN_FOLD_N:
        return {"n": n, "status": "INSUFFICIENT_SAMPLE" if n > 0 else "NO_DATA"}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {
        "n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
        "avg_winner": round(pystats.fmean(wins), 4) if wins else None, "avg_loser": round(pystats.fmean(losses), 4) if losses else None,
        "max_drawdown": _max_drawdown(rs),
    }


def _max_drawdown(rs: list[float]) -> float:
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return round(max_dd, 4)


def _mfe_capture(results, min_mfe=0.3):
    pairs = [(r["realized_r"], r["mfe_r"]) for r in results if r["mfe_r"] >= min_mfe]
    if len(pairs) < MIN_FOLD_N:
        return {"n": len(pairs), "status": "INSUFFICIENT_SAMPLE" if pairs else "NO_DATA"}
    caps = [realized / mfe for realized, mfe in pairs]
    return {"n": len(pairs), "mean": round(pystats.fmean(caps), 4), "median": round(pystats.median(caps), 4)}


def _right_tail(results, threshold):
    cohort = [r for r in results if r["mfe_r"] >= threshold]
    if len(cohort) < 5:
        return {"n": len(cohort), "status": "INSUFFICIENT_SAMPLE"}
    retained = [r["realized_r"] / r["mfe_r"] for r in cohort]
    return {"n": len(cohort), "mean_retention_pct": round(100 * pystats.fmean(retained), 1), "kept_half_or_more": sum(1 for f in retained if f >= 0.5)}


def _report(name, results):
    results = sorted(results, key=lambda r: r["entry_time"])
    all_rs = [r["realized_r"] for r in results]
    cut = len(all_rs) // 2
    print(f"\n{name} (n={len(results)})")
    print(f"  all      {_stats(all_rs)}")
    print(f"  train50  {_stats(all_rs[:cut])}")
    print(f"  oos50    {_stats(all_rs[cut:])}")
    fold_size = len(all_rs) // 3
    if fold_size >= MIN_FOLD_N:
        for i in range(3):
            fold = all_rs[i * fold_size: (i + 1) * fold_size if i < 2 else len(all_rs)]
            print(f"  fold{i+1}    {_stats(fold)}")
    else:
        print(f"  rolling walk-forward: INSUFFICIENT_SAMPLE (n={len(all_rs)})")
    print(f"  mfe_capture(>=0.3R): {_mfe_capture(results)}")
    print(f"  +2R retention: {_right_tail(results, 2.0)}")
    print(f"  +3R retention: {_right_tail(results, 3.0)}")
    premature = sum(1 for r in results if not r["hard_stop_hit"] and r["mfe_r"] > 0.3 and r["realized_r"] < r["mfe_r"] - 0.1)
    roundtrips = sum(1 for r in results if r["hard_stop_hit"] and r["mfe_r"] >= 0.5 and r["realized_r"] <= 0)
    print(f"  premature_exit_rate: {round(premature/len(results),4)}   profitable->stopout roundtrips: {roundtrips}/{len(results)}")
    action_counts = Counter(a["action"] for r in results for a in r["action_log"])
    print(f"  action counts: {dict(action_counts)}")
    return _stats(all_rs)


async def main():
    print("Loading real closed DEMO trades...", flush=True)
    trades = base.load_real_closed_trades()
    print(f"  {len(trades)} trades with deal data", flush=True)

    print("\nRunning Policy A (current manager, unchanged)...", flush=True)
    results_a = await run_policy(trades, be_trigger=None, use_choch_mss=False)
    print("Running Policy B (CHoCH/MSS invalidation only)...", flush=True)
    results_b = await run_policy(trades, be_trigger=None, use_choch_mss=True)
    print("Running Policy C (0.5R breakeven only)...", flush=True)
    results_c = await run_policy(trades, be_trigger=0.5, use_choch_mss=False)
    print("Running Policy D (combined)...", flush=True)
    results_d = await run_policy(trades, be_trigger=0.5, use_choch_mss=True)

    print("\n" + "=" * 110)
    print("FULL-SAMPLE COMPARISON, chronological walk-forward")
    print("=" * 110)
    summary = {}
    summary["A"] = _report("A: current manager (unchanged)", results_a)
    summary["B"] = _report("B: CHoCH/MSS invalidation only", results_b)
    summary["C"] = _report("C: 0.5R breakeven only", results_c)
    summary["D"] = _report("D: combined (CHoCH/MSS + 0.5R BE)", results_d)

    print("\n" + "=" * 110)
    print("COMPLEMENTARITY CHECK")
    print("=" * 110)
    total_a = sum(r["realized_r"] for r in results_a)
    total_b = sum(r["realized_r"] for r in results_b)
    total_c = sum(r["realized_r"] for r in results_c)
    total_d = sum(r["realized_r"] for r in results_d)
    gain_b = total_b - total_a
    gain_c = total_c - total_a
    gain_d = total_d - total_a
    print(f"  total_r: A={round(total_a,2)}  B={round(total_b,2)}  C={round(total_c,2)}  D={round(total_d,2)}")
    print(f"  gain vs A: B={round(gain_b,2)}R  C={round(gain_c,2)}R  D={round(gain_d,2)}R  (sum of B+C individual gains={round(gain_b+gain_c,2)}R)")
    if gain_b + gain_c > 0:
        print(f"  D captures {round(100*gain_d/(gain_b+gain_c),1)}% of the naively-summed individual gains -- {'ADDITIVE/complementary' if gain_d > 0.8*(gain_b+gain_c) else ('REDUNDANT (overlapping benefit)' if gain_d < 0.5*(gain_b+gain_c) else 'partially overlapping')}")

    print("\n" + "=" * 110)
    print("mtfai1-SPECIFIC SLICE (53/78 trades)")
    print("=" * 110)
    for label, results in [("A", results_a), ("B", results_b), ("C", results_c), ("D", results_d)]:
        mtfai1_rows = [r for r in results if r["strategy_id"] == "mtfai1"]
        print(f"  {label}: {_stats([r['realized_r'] for r in mtfai1_rows])}  mfe_capture={_mfe_capture(mtfai1_rows)}")

    print("\n" + "=" * 110)
    print("NON-mtfai1 SLICE (does the change help or hurt the rest of the strategies?)")
    print("=" * 110)
    for label, results in [("A", results_a), ("B", results_b), ("C", results_c), ("D", results_d)]:
        other_rows = [r for r in results if r["strategy_id"] != "mtfai1"]
        print(f"  {label}: {_stats([r['realized_r'] for r in other_rows])}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

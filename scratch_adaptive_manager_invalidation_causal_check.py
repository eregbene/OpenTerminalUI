"""Focused causal check: was CHoCH/MSS genuinely the right invalidation signal, or is
'suppress THESIS_INVALIDATION_CLOSE almost entirely' just better than the current bare rule
regardless of WHICH stronger gate is used? Tests A (current rule) / B (CHoCH/MSS-only) / C
(disabled entirely) / D (hybrid: CHoCH/MSS when enough bars exist, else an early substitute --
structural-level violation, displacement against, or magnitude-scaled consecutive opposing
closes) on the SAME 78 point-in-time-safe trades, chronological walk-forward, with BE held at
the now-deployed 0.5R constant across all four so this isolates the invalidation-rule question
alone. MFE_PROTECTION_CLOSE untouched throughout. Research only -- no production change.
"""
from __future__ import annotations

import statistics as pystats
from collections import Counter

import scratch_adaptive_manager_combined_validation as combined
import scratch_adaptive_manager_forensic_audit as base
import scratch_adaptive_manager_mfe_invalidation_deep_audit as deep
from backend.market_structure.engine import analyze_bars
from backend.market_structure.models import StructureBreakKind

svc = base.svc
MIN_FOLD_N = 15
BE_CONSTANT = 0.5  # matches the now-deployed production value


def _structural_evidence(window, direction: str, symbol: str) -> dict:
    try:
        rows = [{"time": w["time"], "open": w["open"], "high": w["high"], "low": w["low"], "close": w["close"]} for w in window]
        if len(rows) < 8:
            return {"enough_bars_for_choch_mss": False, "choch_mss_against": False, "displacement_against": False, "level_violation_against": False}
        snapshot = analyze_bars(rows, symbol=symbol, timeframe="M15")
        against_bias = "bearish" if direction == "LONG" else "bullish"
        recent_window = max(0, len(rows) - 6)
        choch_mss_against = any(b.break_kind in (StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value) and b.direction == against_bias and b.bar_index >= recent_window for b in snapshot.breaks)
        displacement_against = any(d.bar_index >= recent_window and d.direction == against_bias for d in snapshot.displacements)
        # Structural-level violation: nearest liquidity level on the AGAINST side (support for a
        # LONG, resistance for a SHORT) that the most recent close has now broken through.
        against_side = "sell_side" if direction == "LONG" else "buy_side"
        candidate_levels = [lvl for lvl in snapshot.liquidity_levels if lvl.side == against_side]
        level_violation_against = False
        if candidate_levels:
            last_close = float(rows[-1]["close"])
            nearest = min(candidate_levels, key=lambda lv: abs(float(lv.level) - last_close))
            level_price = float(nearest.level)
            level_violation_against = (last_close < level_price) if direction == "LONG" else (last_close > level_price)
        return {"enough_bars_for_choch_mss": len(rows) >= 15, "choch_mss_against": choch_mss_against, "displacement_against": displacement_against, "level_violation_against": level_violation_against}
    except Exception:
        return {"enough_bars_for_choch_mss": False, "choch_mss_against": False, "displacement_against": False, "level_violation_against": False}


def _magnitude_scaled_opposing_closes(window, direction: str, atr: float | None) -> bool:
    """Stronger version of the bare 'opposing_candles>=2' rule: the CUMULATIVE adverse close-to-
    close move over the trailing opposing run must also clear an ATR-scaled bar (0.5x), not just
    2 candles in the same direction regardless of size -- directly targets 'is this a genuine
    deterioration or a normal small pullback/retest'."""
    if not atr or atr <= 0 or len(window) < 3:
        return False
    long = direction == "LONG"
    closes = [w["close"] for w in window[-4:]]
    run = 0
    cumulative_move = 0.0
    for i in range(len(closes) - 1, 0, -1):
        bearish = closes[i] < closes[i - 1]
        bullish = closes[i] > closes[i - 1]
        opposing = bearish if long else bullish
        if not opposing:
            break
        run += 1
        cumulative_move += abs(closes[i] - closes[i - 1])
    return run >= 2 and cumulative_move >= 0.5 * atr


def make_filter(mode: str, symbol: str):
    def _filter(bar, window, candidates, state):
        if mode == "disabled":
            return [c for c in candidates if c.action_type != "THESIS_INVALIDATION_CLOSE"] or candidates
        out = []
        for c in candidates:
            if c.action_type != "THESIS_INVALIDATION_CLOSE":
                out.append(c)
                continue
            if mode == "A":
                out.append(c)
                continue
            if mode == "B":
                ev = _structural_evidence(window, state.direction, symbol)
                if ev["choch_mss_against"]:
                    out.append(c)
                continue
            if mode == "D_hybrid":
                ev = _structural_evidence(window, state.direction, symbol)
                atr = float(svc.detect_regime(window, context={}).get("features", {}).get("atr") or 0) or None
                if ev["enough_bars_for_choch_mss"] and ev["choch_mss_against"]:
                    out.append(c)
                elif not ev["enough_bars_for_choch_mss"] and (ev["level_violation_against"] or ev["displacement_against"] or _magnitude_scaled_opposing_closes(window, state.direction, atr)):
                    out.append(c)
                # else: dropped -- no qualifying evidence, falls through (often HOLD)
        return out
    return _filter


def _stats(rs):
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
        "max_drawdown": _max_drawdown(rs),
    }


def _max_drawdown(rs):
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
    return {"n": len(cohort), "mean_retention_pct": round(100 * pystats.fmean(retained), 1)}


def _report(name, results):
    results = sorted(results, key=lambda r: r["entry_time"])
    all_rs = [r["realized_r"] for r in results]
    cut = len(all_rs) // 2
    print(f"\n{name} (n={len(results)})  total_r={round(sum(all_rs),3)}")
    print(f"  all      {_stats(all_rs)}")
    print(f"  train50  {_stats(all_rs[:cut])}")
    print(f"  oos50    {_stats(all_rs[cut:])}")
    fold_size = len(all_rs) // 3
    if fold_size >= MIN_FOLD_N:
        for i in range(3):
            fold = all_rs[i * fold_size: (i + 1) * fold_size if i < 2 else len(all_rs)]
            print(f"  fold{i+1}    {_stats(fold)}")
    print(f"  mfe_capture(>=0.3R): {_mfe_capture(results)}")
    print(f"  +2R retention: {_right_tail(results, 2.0)}")
    action_counts = Counter(a["action"] for r in results for a in r["action_log"])
    print(f"  action counts: {dict(action_counts)}")
    return sum(all_rs)


async def run_mode(trades, mode: str):
    svc_instance = svc.AdaptiveManagementService()
    db = deep.SessionLocal()
    import os
    os.environ["ADAPTIVE_BREAKEVEN_R"] = str(BE_CONSTANT)
    results = []
    try:
        for trade in trades:
            try:
                candles = await base.fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                sf = make_filter(mode, trade["symbol"])
                r = combined.reconstruct_policy(trade, candles, svc_instance, db, structural_filter=sf)
                if r is not None:
                    results.append(r)
            except Exception as exc:
                print(f"  skipped {trade['trade_id']}: {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        db.rollback()
        db.close()
    return results


async def main():
    trades = base.load_real_closed_trades()
    print(f"{len(trades)} trades with deal data. BE held constant at {BE_CONSTANT}R across all four modes.", flush=True)

    print("\nRunning A (current bare opposing-candle rule)...", flush=True)
    results_a = await run_mode(trades, "A")
    print("Running B (CHoCH/MSS-only gate)...", flush=True)
    results_b = await run_mode(trades, "B")
    print("Running C (THESIS_INVALIDATION_CLOSE disabled entirely)...", flush=True)
    results_c = await run_mode(trades, "disabled")
    print("Running D (hybrid early-invalidity rule)...", flush=True)
    results_d = await run_mode(trades, "D_hybrid")

    print("\n" + "=" * 110)
    print("FULL-SAMPLE COMPARISON (BE=0.5R constant across all four)")
    print("=" * 110)
    total_a = _report("A: current bare opposing-candle rule", results_a)
    total_b = _report("B: CHoCH/MSS-only gate", results_b)
    total_c = _report("C: THESIS_INVALIDATION_CLOSE disabled entirely", results_c)
    total_d = _report("D: hybrid early-invalidity rule", results_d)

    print("\n" + "=" * 110)
    print("CAUSAL CHECK: is B better because CHoCH/MSS is the right signal, or just because it suppresses the rule almost entirely?")
    print("=" * 110)
    print(f"  total_r: A={round(total_a,2)}  B={round(total_b,2)}  C(disabled)={round(total_c,2)}  D(hybrid)={round(total_d,2)}")
    b_vs_c_gap = total_b - total_c
    print(f"  B vs C (disabled) gap: {round(b_vs_c_gap,3)}R -- {'B meaningfully beats outright disabling: CHoCH/MSS carries real incremental signal' if abs(b_vs_c_gap) > 0.5 else 'B and C are close: suppression alone explains most of the improvement, CHoCH/MSS specifically is NOT proven causal'}")
    print(f"  D vs B: {round(total_d - total_b,3)}R  D vs C: {round(total_d - total_c,3)}R")
    print(f"  D vs A (current production): {round(total_d - total_a,3)}R")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

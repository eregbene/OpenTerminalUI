"""Focused forensic deep-audit of MFE_PROTECTION_CLOSE and THESIS_INVALIDATION_CLOSE -- the two
actions the prior audit identified as the clearest measured profit leakage -- plus a dedicated
mtfai1 (53/77 trades, -0.074R expectancy) breakdown. Per explicit instruction: no new indicators;
structural confirmation reuses Bensim's EXISTING market_structure.engine.analyze_bars (BOS/CHoCH/
MSS/displacement), called only at the specific bars where the base opposing-candle rule is
already true (not every cycle) to keep this tractable.

Reuses scratch_adaptive_manager_forensic_audit.py's tested building blocks (real
_evaluate_position/_select_action, real candle loading, real state bookkeeping) -- imported as a
module, not re-copied.

Nothing here writes to any table or touches production code/behavior.
"""
from __future__ import annotations

import statistics as pystats
from collections import Counter, defaultdict

import scratch_adaptive_manager_forensic_audit as base
from backend.adaptive_management.orm import AdaptivePartialExitStageORM
from backend.market_structure.engine import analyze_bars
from backend.market_structure.models import StructureBreakKind
from backend.shared.db import SessionLocal

svc = base.svc
tp_protection = base.tp_protection

CLOSING_ACTION_TYPES = {
    "MFE_PROTECTION_CLOSE", "THESIS_INVALIDATION_CLOSE", "PARTIAL_PROFIT", "TP_PROGRESS_PARTIAL_PROTECT",
    "TP_PROGRESS_PROFIT_LOCK", "TIME_EXIT", "EVENT_RISK_REDUCTION", "ECONOMIC_REDUCE_SIZE",
}


# --------------------------------------------------------------- suppression-aware replay ---
def reconstruct_with_suppression(trade, candles, svc_instance, db, *, suppress_types: set[str] = frozenset(), structural_filter=None):
    """Same state machine as base.reconstruct_forensics, extended with two knobs:
      - suppress_types: candidates of these action_types are removed before _select_action every
        cycle (falls back to the next-highest-priority remaining candidate, e.g. HOLD).
      - structural_filter(bar, window, candidates) -> list[candidates]: optional per-cycle hook,
        used here to require additional real market_structure confirmation before
        THESIS_INVALIDATION_CLOSE survives into the selection pool.
    Returns the same shape as base.reconstruct_forensics, plus 'trigger_events' logging exactly
    when/why any of CLOSING_ACTION_TYPES fired (or would have, before suppression), with the
    forensic state at that instant.
    """
    normalized = base._normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    state = base._new_state(trade)
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    long = state.direction == "LONG"

    mfe_r, mae_r = 0.0, 0.0
    for bar in normalized:
        bar_high_r = base._signed_r(state.entry_price, bar["high"] if long else bar["low"], risk, state.direction)
        bar_low_r = base._signed_r(state.entry_price, bar["low"] if long else bar["high"], risk, state.direction)
        mfe_r = max(mfe_r, bar_high_r)
        mae_r = min(mae_r, bar_low_r)

    legs: list[dict] = []
    action_log: list[dict] = []
    trigger_events: list[dict] = []
    final_r, hard_stop_hit, natural_end = 0.0, False, True

    for i, bar in enumerate(normalized):
        window = normalized[max(0, i - base.REGIME_LOOKBACK_BARS):i + 1]
        sl_touched = (bar["low"] <= state.current_sl) if long else (bar["high"] >= state.current_sl)
        tp_touched = (bar["high"] >= state.current_tp) if long else (bar["low"] <= state.current_tp)
        if sl_touched or tp_touched:
            touch_price = state.current_sl if sl_touched else state.current_tp
            final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=touch_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
            hard_stop_hit, natural_end = True, False
            break

        r_now, regime_info, atr, _ = base._update_state_for_cycle(state, current_price=bar["close"], candles_window=window)
        payload = {"price_current": bar["close"], "price_open": state.entry_price, "sl": state.current_sl, "tp": state.current_tp, "volume": state.current_volume, "profit": None, "symbol": state.symbol}
        candidates = svc_instance._evaluate_position(db, state, payload, {}, window, economic_result=None, symbol_info=None, account_equity=None)

        for c in candidates:
            if c.action_type in CLOSING_ACTION_TYPES:
                trigger_events.append({
                    "trade_id": trade["trade_id"], "bar_index": i, "time": bar["time"], "action_type": c.action_type, "reason": c.reason,
                    "r_now": r_now, "mfe_r_so_far": max(base._signed_r(state.entry_price, b["high"] if long else b["low"], risk, state.direction) for b in normalized[:i + 1]),
                    "giveback_r_so_far": state.current_giveback_r, "trade_age_bars": i, "winner_classification": state.winner_classification,
                })

        if structural_filter is not None:
            candidates = structural_filter(bar, window, candidates, state)
        if suppress_types:
            candidates = [c for c in candidates if c.action_type not in suppress_types] or candidates
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
        legs.append({"fraction": remaining_fraction, "r": final_r})
    blended_r = sum(leg["fraction"] * leg["r"] for leg in legs)

    return {
        "trade_id": trade["trade_id"], "symbol": trade["symbol"], "strategy_id": trade["strategy_id"], "entry_time": trade["entry_time"],
        "mfe_r": round(mfe_r, 4), "realized_r": round(blended_r, 4), "action_log": action_log, "trigger_events": trigger_events,
        "hard_stop_hit": hard_stop_hit, "normalized": normalized,
    }


# ---------------------------------------------------------- THESIS_INVALIDATION structural check
def _structural_confirmation_against(bar_time, window, direction: str, symbol: str) -> dict:
    """Runs the REAL market_structure.engine.analyze_bars (no new indicator) on the trade's own
    accumulated bar window, checks for a CHoCH/MSS break, a displacement event, or a fresh
    liquidity sweep in the recent window that is AGAINST the trade direction."""
    try:
        rows = [{"time": w["time"], "open": w["open"], "high": w["high"], "low": w["low"], "close": w["close"]} for w in window]
        if len(rows) < 15:
            return {"choch_mss_against": False, "displacement_against": False, "sweep_against": False}
        snapshot = analyze_bars(rows, symbol=symbol, timeframe="M15")
        against_bias = "bearish" if direction == "LONG" else "bullish"
        recent_window = max(0, len(rows) - 6)
        choch_mss_against = any(
            b.break_kind in (StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value) and b.direction == against_bias and b.bar_index >= recent_window
            for b in snapshot.breaks
        )
        displacement_against = any(d.bar_index >= recent_window and d.direction == against_bias for d in snapshot.displacements)
        sweep_against = any(s.bar_index >= recent_window and s.direction == against_bias for s in snapshot.liquidity_sweeps)
        return {"choch_mss_against": choch_mss_against, "displacement_against": displacement_against, "sweep_against": sweep_against}
    except Exception:
        return {"choch_mss_against": False, "displacement_against": False, "sweep_against": False}


def make_structural_filter(mode: str, symbol: str):
    """mode in {'choch_mss', 'consecutive3', 'displacement', 'combo'} -- returns a
    structural_filter callback for reconstruct_with_suppression that REQUIRES the extra
    confirmation before THESIS_INVALIDATION_CLOSE survives; falls through to whatever the real
    engine would otherwise select (HOLD, etc.) when the stronger condition isn't met."""
    def _filter(bar, window, candidates, state):
        out = []
        for c in candidates:
            if c.action_type != "THESIS_INVALIDATION_CLOSE":
                out.append(c)
                continue
            if mode == "consecutive3":
                if svc._opposing_candles(window, state.direction) >= 3:
                    out.append(c)
                continue
            confirmation = _structural_confirmation_against(bar["time"], window, state.direction, symbol)
            if mode == "choch_mss" and confirmation["choch_mss_against"]:
                out.append(c)
            elif mode == "displacement" and confirmation["displacement_against"]:
                out.append(c)
            elif mode == "combo" and (confirmation["choch_mss_against"] or confirmation["displacement_against"] or confirmation["sweep_against"]):
                out.append(c)
            # else: dropped -- falls through to next-best candidate (often HOLD)
        return out
    return _filter


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "status": "NO_DATA"}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {"n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4), "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf}


def _classify_mfe_protection_trigger(event: dict, normalized: list[dict], entry: float, risk: float, direction: str) -> dict:
    long = direction == "LONG"
    idx = event["bar_index"]
    subsequent = normalized[idx + 1:]
    r_at_trigger = event["r_now"]
    if not subsequent:
        return {**event, "late": event["giveback_r_so_far"] >= 0.5 * event["mfe_r_so_far"] if event["mfe_r_so_far"] else False, "classification": "no_subsequent_data"}
    max_subsequent_r = max(base._signed_r(entry, b["high"] if long else b["low"], risk, direction) for b in subsequent)
    late = (event["giveback_r_so_far"] >= 0.5 * event["mfe_r_so_far"]) if event["mfe_r_so_far"] else False
    if max_subsequent_r - r_at_trigger >= 0.3:
        classification = "premature_price_resumed"
    elif max_subsequent_r <= r_at_trigger + 0.1:
        classification = "correct_save"
    else:
        classification = "ambiguous"
    return {**event, "late": late, "classification": classification, "max_subsequent_r": round(max_subsequent_r, 4)}


async def main():
    print("Loading real closed DEMO trades...", flush=True)
    trades = base.load_real_closed_trades()
    print(f"  {len(trades)} trades with deal data", flush=True)

    svc_instance = svc.AdaptiveManagementService()
    db = SessionLocal()
    actual_results: dict[str, dict] = {}
    hold_instead_mfe: dict[str, dict] = {}
    hold_instead_invalidation: dict[str, dict] = {}
    static_results: dict[str, float] = {}
    be_only_results: dict[str, dict] = {}
    mfe_trigger_events: list[dict] = []
    invalidation_trigger_events: list[dict] = []

    try:
        for trade in trades:
            try:
                candles = await base.fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                actual = reconstruct_with_suppression(trade, candles, svc_instance, db)
                if actual is None:
                    continue
                actual_results[trade["trade_id"]] = actual

                case, path = base._case_and_path(trade, candles)
                if case is not None:
                    static_results[trade["trade_id"]] = base.run_shadow_family(case, path, "static", {})

                be_only = reconstruct_with_suppression(trade, candles, svc_instance, db, suppress_types=CLOSING_ACTION_TYPES)
                if be_only is not None:
                    be_only_results[trade["trade_id"]] = be_only

                fired_types = {a["action"] for a in actual["action_log"]}
                if "MFE_PROTECTION_CLOSE" in fired_types:
                    hold_instead_mfe[trade["trade_id"]] = reconstruct_with_suppression(trade, candles, svc_instance, db, suppress_types={"MFE_PROTECTION_CLOSE"})
                    first = next((e for e in actual["trigger_events"] if e["action_type"] == "MFE_PROTECTION_CLOSE"), None)
                    if first:
                        risk = abs(float(trade["entry"]) - float(trade["stop_loss"])) or 1e-5
                        mfe_trigger_events.append(_classify_mfe_protection_trigger(first, actual["normalized"], float(trade["entry"]), risk, trade["direction"]))

                if "THESIS_INVALIDATION_CLOSE" in fired_types:
                    hold_instead_invalidation[trade["trade_id"]] = reconstruct_with_suppression(trade, candles, svc_instance, db, suppress_types={"THESIS_INVALIDATION_CLOSE"})
                    first = next((e for e in actual["trigger_events"] if e["action_type"] == "THESIS_INVALIDATION_CLOSE"), None)
                    if first:
                        invalidation_trigger_events.append(first)
            except Exception as exc:
                print(f"  skipped {trade['trade_id']} ({trade['symbol']}): {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        db.rollback()
        db.close()

    print(f"\n{len(actual_results)} trades reconstructed. MFE_PROTECTION_CLOSE fired in {len(hold_instead_mfe)}. THESIS_INVALIDATION_CLOSE fired in {len(hold_instead_invalidation)}.", flush=True)

    # ================================================================ MFE_PROTECTION_CLOSE ====
    print("\n" + "=" * 110)
    print("PART 1: MFE_PROTECTION_CLOSE deep audit")
    print("=" * 110)
    print(f"\n  Trigger-time context (n={len(mfe_trigger_events)}):")
    if mfe_trigger_events:
        print(f"    mean mfe_r_so_far={round(pystats.fmean(e['mfe_r_so_far'] for e in mfe_trigger_events),3)}  mean r_now_at_trigger={round(pystats.fmean(e['r_now'] for e in mfe_trigger_events),3)}  mean giveback_so_far={round(pystats.fmean(e['giveback_r_so_far'] for e in mfe_trigger_events),3)}  mean trade_age_bars={round(pystats.fmean(e['trade_age_bars'] for e in mfe_trigger_events),1)}")
        classification_counts = Counter(e["classification"] for e in mfe_trigger_events)
        print(f"\n  Classification: {dict(classification_counts)}")
        late_count = sum(1 for e in mfe_trigger_events if e["late"])
        print(f"  'Late' protection (>=50% of peak already given back before firing): {late_count}/{len(mfe_trigger_events)}")
        for cls in ("correct_save", "premature_price_resumed", "ambiguous", "no_subsequent_data"):
            subset = [e for e in mfe_trigger_events if e["classification"] == cls]
            if subset:
                print(f"\n  {cls} (n={len(subset)}):")
                for e in subset[:8]:
                    print(f"    {e['trade_id']}: mfe_so_far={round(e['mfe_r_so_far'],3)} r_at_trigger={round(e['r_now'],3)} max_subsequent_r={e.get('max_subsequent_r')} late={e['late']}")

    print("\n  Actual vs HOLD-instead vs static vs BE-only-protection, for trades where MFE_PROTECTION_CLOSE fired:")
    ids = list(hold_instead_mfe.keys())
    actual_rs = [actual_results[t]["realized_r"] for t in ids]
    hold_rs = [hold_instead_mfe[t]["realized_r"] for t in ids]
    static_rs = [static_results[t] for t in ids if t in static_results]
    be_rs = [be_only_results[t]["realized_r"] for t in ids if t in be_only_results]
    print(f"    actual (MFE_PROTECTION_CLOSE fires): {_stats(actual_rs)}   total_r={round(sum(actual_rs),3)}")
    print(f"    hold-instead (suppressed):           {_stats(hold_rs)}   total_r={round(sum(hold_rs),3)}")
    print(f"    static SL/TP only:                   {_stats(static_rs)}   total_r={round(sum(static_rs),3)}")
    print(f"    BE/protection-only (no closes):      {_stats(be_rs)}   total_r={round(sum(be_rs),3)}")
    print(f"    R saved by MFE_PROTECTION_CLOSE vs hold-instead: {round(sum(actual_rs) - sum(hold_rs), 3)}R  (positive = the action helped on net; negative = it cost R on net)")

    # ============================================================ THESIS_INVALIDATION_CLOSE ===
    print("\n" + "=" * 110)
    print("PART 2: THESIS_INVALIDATION_CLOSE deep audit")
    print("=" * 110)
    print(f"\n  Base rule (opposing_candles>=2 AND r_now<-0.25) trigger-time context (n={len(invalidation_trigger_events)}):")
    if invalidation_trigger_events:
        print(f"    mean r_now_at_trigger={round(pystats.fmean(e['r_now'] for e in invalidation_trigger_events),3)}  mean trade_age_bars={round(pystats.fmean(e['trade_age_bars'] for e in invalidation_trigger_events),1)}")

    ids2 = list(hold_instead_invalidation.keys())
    actual_rs2 = [actual_results[t]["realized_r"] for t in ids2]
    hold_rs2 = [hold_instead_invalidation[t]["realized_r"] for t in ids2]
    static_rs2 = [static_results[t] for t in ids2 if t in static_results]
    be_rs2 = [be_only_results[t]["realized_r"] for t in ids2 if t in be_only_results]
    print(f"\n  Actual vs HOLD-instead vs static vs BE-only-protection, for trades where THESIS_INVALIDATION_CLOSE fired:")
    print(f"    actual (invalidation fires):    {_stats(actual_rs2)}   total_r={round(sum(actual_rs2),3)}")
    print(f"    hold-instead (suppressed):      {_stats(hold_rs2)}   total_r={round(sum(hold_rs2),3)}")
    print(f"    static SL/TP only:              {_stats(static_rs2)}   total_r={round(sum(static_rs2),3)}")
    print(f"    BE/protection-only (no closes): {_stats(be_rs2)}   total_r={round(sum(be_rs2),3)}")
    print(f"    R saved by THESIS_INVALIDATION_CLOSE vs hold-instead: {round(sum(actual_rs2) - sum(hold_rs2), 3)}R")

    print("\n  Stronger-confirmation variants (replayed across ALL trades, real market_structure.engine, no new indicators):")
    for mode in ("consecutive3", "choch_mss", "displacement", "combo"):
        variant_rs = []
        db2 = SessionLocal()
        try:
            for trade in trades:
                if trade["trade_id"] not in actual_results:
                    continue
                candles = await base.fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                sf = make_structural_filter(mode, trade["symbol"])
                result = reconstruct_with_suppression(trade, candles, svc_instance, db2, structural_filter=sf)
                if result is not None:
                    variant_rs.append(result["realized_r"])
        finally:
            db2.rollback()
            db2.close()
        print(f"    {mode:14s} {_stats(variant_rs)}   total_r={round(sum(variant_rs),3) if variant_rs else None}")
    baseline_all_rs = [actual_results[t]["realized_r"] for t in actual_results]
    print(f"    {'baseline (real rule)':14s} {_stats(baseline_all_rs)}   total_r={round(sum(baseline_all_rs),3)}")

    # ================================================================================ mtfai1 ===
    print("\n" + "=" * 110)
    print("PART 3: mtfai1-specific breakdown (53/77 trades, -0.074R under current manager)")
    print("=" * 110)
    mtfai1_ids = [t for t in actual_results if actual_results[t]["strategy_id"] == "mtfai1"]
    print(f"\n  n={len(mtfai1_ids)}")
    a_rs = [actual_results[t]["realized_r"] for t in mtfai1_ids]
    s_rs = [static_results[t] for t in mtfai1_ids if t in static_results]
    b_rs = [be_only_results[t]["realized_r"] for t in mtfai1_ids if t in be_only_results]
    print(f"    A (current manager):        {_stats(a_rs)}")
    print(f"    B (static SL/TP only):      {_stats(s_rs)}")
    print(f"    BE/protection-only:         {_stats(b_rs)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

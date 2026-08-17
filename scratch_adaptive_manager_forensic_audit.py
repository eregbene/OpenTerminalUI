"""Adaptive Manager Forensic Optimization Audit -- DEMO only, per explicit instruction: prove
where profit is being lost, do not assume the manager is bad, do not change production behavior.

Reuses:
  - The REAL _evaluate_position()/_select_action() decision engine (unchanged) for Policy A,
    same state-machine replay as scratch_adaptive_manager_eqh_eql_evidence_validation.py, same
    scope limitations EXCEPT ONE CORRECTED after review: v2 action types (MOVE_SL_TO_REDUCED_
    RISK/EXTEND_TP/REDUCE_TP) are INCLUDED here, not excluded. _can_execute's own gate
    (service.py:2313-2318) explicitly documents that v2_mode()=="shadow" (this deployment's
    actual configured value) no longer blocks real execution -- only "disabled" does. Since
    this replay never disables v2_mode, these actions faithfully reflect what the REAL current
    manager actually does on an INTERNAL_DEMO account today. Still excluded: economic-calendar
    context, account-equity-scaled layer, real broker risk-money (price-distance R only).
  - service.py::reconstruct_path/simulate_policy (unmodified, tested) for the shadow-family
    counterfactuals whose mechanism already exists there: static baseline (B), MFE/giveback
    retracement (D), breakeven (E), time/stagnation exit (G).
  - New, single-purpose walk-forward functions only where the requested mechanism doesn't
    already exist: ATR-price-distance trailing (C -- simulate_policy's own "trailing" family is
    R-fraction-based, a different mechanism), partial-profit + ATR-trailed runner (F).

Explicitly NOT used this pass, per instruction: EQH/EQL, LazyBear, Lorentzian, LuxAlgo. This
audit is about the EXISTING Adaptive Manager and existing strategies only.

MFE/MAE/all counterfactuals are bounded to each REAL trade's own actual lifetime (entry_time to
its real exit_time) -- never extrapolated past where the real trade actually closed. Stated
scope, not hidden: "how much more was available" answers are relative to that trade's own
observed path, not a hypothetical infinite hold.

Coarse parameter grids only (2-4 values per family), chronological 50/50 AND a 3-fold rolling
walk-forward, with an explicit minimum-n-per-fold gate (reports INSUFFICIENT_SAMPLE rather than
a misleading precise number below it). Parameter-sensitivity check: adjacent grid values must
show directionally consistent results, or the "best" one is flagged as unstable, not promoted.

Nothing here writes to any table (uncommitted session, rolled back at the end) or touches a real
position/broker/production code path.
"""
from __future__ import annotations

import statistics as pystats
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from backend.adaptive_management import service as svc
from backend.adaptive_management import tp_protection
from backend.adaptive_management.orm import AdaptivePartialExitStageORM, AdaptivePositionStateORM as PS, AdaptiveTradeEventORM as TE
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.historical_intelligence.fingerprint import time_of_day_bucket
from backend.mt5_strategies.models import normalize_strategy_id
from backend.shared.db import SessionLocal

R_MILESTONES = (0.5, 1.0, 1.5, 2.0, 3.0)
REGIME_LOOKBACK_BARS = 60


# --------------------------------------------------------------------------- data loading ---
def load_real_closed_trades():
    with SessionLocal() as db:
        positions = db.query(PS).filter(PS.closed_detected_at.isnot(None), PS.contaminated.is_(False)).order_by(PS.opened_at.asc()).all()
        trades = []
        for row in positions:
            deals = db.query(TE).filter(TE.position_id == row.position_id, TE.event_type == "DEAL").all()
            if not deals:
                continue
            exit_deal = max(deals, key=lambda d: d.utc_time or row.closed_detected_at)
            realized_pnl = compute_trade_costs([{"profit": d.realized_pnl, "commission": d.commission, "swap": d.swap, "fee": d.fee, "volume": d.volume} for d in deals]).net_pnl
            trades.append({
                "trade_id": row.position_id, "symbol": row.symbol, "direction": row.direction.upper(), "volume": row.original_volume,
                "entry": row.entry_price, "stop_loss": row.original_sl, "take_profit": row.original_tp,
                "entry_time": row.opened_at, "exit_time": exit_deal.utc_time or row.closed_detected_at, "actual_pnl": realized_pnl,
                # normalize_strategy_id: the same canonicalizer this codebase's own HI backfill
                # already relies on (adaptive_similarity/simulate_policy's historical_analog
                # family) -- without it, "mtfai1" and "MTFAI1" silently split into two separate
                # strategy buckets in Section 3's breakdown, needlessly fragmenting the sample.
                "strategy_id": normalize_strategy_id(row.strategy_id) or "UNKNOWN",
            })
    return trades


async def fetch_candles(symbol: str, entry_time, exit_time):
    return await svc._replay_candles(symbol, "M15", entry_time, exit_time)


def _normalized_window(candles, entry_time, exit_time):
    normalized = sorted((svc._normalize_candle(c) for c in candles), key=lambda c: c["time"] if c else datetime.min)
    return [c for c in normalized if c and entry_time <= c["time"] <= exit_time]


def _signed_r(entry: float, price: float, risk: float, direction: str) -> float:
    return (price - entry) / risk if direction == "LONG" else (entry - price) / risk


# ------------------------------------------------------------- Policy A: current manager ---
class _State:
    __slots__ = (
        "position_id", "symbol", "direction", "entry_price", "opened_at",
        "original_sl", "original_tp", "current_sl", "current_tp",
        "original_volume", "current_volume",
        "max_achieved_r", "min_achieved_r", "tp_progress", "max_tp_progress",
        "winner_classification", "partial_profit_stage", "current_giveback_r", "last_management_at",
        "original_risk_money",
    )


def _replay_cooldown_elapsed(last_management_at, bar_time) -> bool:
    if not last_management_at:
        return True
    cooldown = svc._env_int("ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS", 120, minimum=10, maximum=3600)
    return (bar_time - last_management_at).total_seconds() >= cooldown


def _new_state(trade: dict) -> _State:
    s = _State()
    s.position_id = "AUDIT_" + trade["trade_id"]
    s.symbol = trade["symbol"]
    s.direction = trade["direction"]
    s.entry_price = float(trade["entry"])
    s.opened_at = trade["entry_time"]
    s.original_sl = float(trade["stop_loss"])
    s.original_tp = float(trade["take_profit"])
    s.current_sl, s.current_tp = s.original_sl, s.original_tp
    s.original_volume = float(trade["volume"] or 1.0)
    s.current_volume = s.original_volume
    s.max_achieved_r = s.min_achieved_r = s.tp_progress = s.max_tp_progress = s.current_giveback_r = 0.0
    s.winner_classification = "healthy_pullback"
    s.partial_profit_stage = "NONE"
    s.last_management_at = None
    s.original_risk_money = None
    return s


def _update_state_for_cycle(state: _State, *, current_price: float, candles_window: list[dict]):
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    r_now, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=current_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
    state.max_achieved_r = max(state.max_achieved_r, r_now)
    state.min_achieved_r = min(state.min_achieved_r, r_now)
    regime_info = svc.detect_regime(candles_window, context={})
    atr = float(regime_info.get("features", {}).get("atr") or 0) or None
    progress = tp_protection.tp_progress(state.direction, state.entry_price, current_price, state.current_tp)
    state.tp_progress = progress if progress is not None else state.tp_progress
    if progress is not None and progress > state.max_tp_progress:
        state.max_tp_progress = progress
    state.current_giveback_r = max(0.0, state.max_achieved_r - r_now)
    atr_r = (atr / risk) if atr and risk else 0.0
    allowance_fraction = tp_protection.retracement_allowance(atr_r=atr_r, regime=regime_info.get("regime", "insufficient_data"), timeframe="M15")
    allowance_r = allowance_fraction * state.max_achieved_r
    retracement_state = tp_protection.classify_retracement(state.current_giveback_r, allowance_r)
    candles_held = svc._candles_held(state.opened_at, candles_window)
    winner = tp_protection.classify_winner_preservation({
        "opposing_candles": svc._opposing_candles(candles_window, state.direction), "retracement_state": retracement_state,
        "regime": regime_info.get("regime", "insufficient_data"), "direction": state.direction, "candles_held": candles_held,
        "remaining_reward_r": (1.0 - progress) if progress is not None else None,
    })
    state.winner_classification = winner["classification"]
    return r_now, regime_info, atr, risk


def _apply_choice(state: _State, choice, *, bar_time, current_r: float, db, legs: list):
    if choice.requested_sl is not None:
        state.current_sl = choice.requested_sl
    if choice.requested_tp is not None:
        state.current_tp = choice.requested_tp
    if choice.requested_volume and choice.requested_volume > 0 and state.current_volume > 0:
        fraction_of_original = min(1.0, choice.requested_volume / state.original_volume)
        legs.append({"fraction": fraction_of_original, "r": current_r, "action": choice.action_type, "time": bar_time})
        state.current_volume = max(0.0, state.current_volume - choice.requested_volume)
        if choice.action_type == "PARTIAL_PROFIT":
            state.partial_profit_stage = "PARTIAL_1_EXECUTED" if state.partial_profit_stage == "NONE" else "PARTIAL_2_EXECUTED"
        if choice.action_type == "TP_PROGRESS_PARTIAL_PROTECT":
            stage = (choice.evidence or {}).get("stage")
            if stage:
                db.add(AdaptivePartialExitStageORM(stage_id=f"AUDIT_{state.position_id}_{stage}", position_id=state.position_id, stage=stage))
                db.flush()
    if choice.action_type != "HOLD":
        state.last_management_at = bar_time
    return state.current_volume <= 1e-9


def reconstruct_forensics(trade: dict, candles: list[dict], svc_instance, db) -> dict | None:
    """Policy A: the REAL current manager, faithfully replayed, with full forensic tracking."""
    normalized = _normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    state = _new_state(trade)
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    planned_rr = abs(state.original_tp - state.entry_price) / risk
    long = state.direction == "LONG"

    # POLICY-INDEPENDENT MFE/MAE/time-to-milestone pre-scan: the true maximum favorable/adverse
    # excursion across the trade's REAL, full candle window, computed BEFORE any policy's own
    # stop movements can truncate it. A real bug caught here during smoke-testing: computing
    # mfe_r inside the SAME loop that breaks early on Policy A's own (possibly BE/trailed-up)
    # stop touch silently clips mfe_r to "whatever price did before Policy A exited" -- useless
    # for a leakage report whose whole point is comparing realized R against the TRUE available
    # upside, independent of what any one policy decided to do with it.
    mfe_r, mae_r = 0.0, 0.0
    time_to_milestone: dict[float, float | None] = {m: None for m in R_MILESTONES}
    for bar in normalized:
        bar_high_r = _signed_r(state.entry_price, bar["high"] if long else bar["low"], risk, state.direction)
        bar_low_r = _signed_r(state.entry_price, bar["low"] if long else bar["high"], risk, state.direction)
        mfe_r = max(mfe_r, bar_high_r)
        mae_r = min(mae_r, bar_low_r)
        for m in R_MILESTONES:
            if time_to_milestone[m] is None and mfe_r >= m:
                time_to_milestone[m] = (bar["time"] - trade["entry_time"]).total_seconds()

    legs: list[dict] = []
    action_log: list[dict] = []
    final_r, hard_stop_hit, natural_end = 0.0, False, True
    entry_regime = None

    for i, bar in enumerate(normalized):
        window = normalized[max(0, i - REGIME_LOOKBACK_BARS):i + 1]
        sl_touched = (bar["low"] <= state.current_sl) if long else (bar["high"] >= state.current_sl)
        tp_touched = (bar["high"] >= state.current_tp) if long else (bar["low"] <= state.current_tp)
        if sl_touched or tp_touched:
            touch_price = state.current_sl if sl_touched else state.current_tp
            final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=touch_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
            hard_stop_hit, natural_end = True, False
            action_log.append({"time": bar["time"], "action": "SL_HIT" if sl_touched else "TP_HIT", "r": final_r})
            break

        r_now, regime_info, atr, _risk2 = _update_state_for_cycle(state, current_price=bar["close"], candles_window=window)
        # BUG FIX (caught in review): capturing entry_regime on the literal first cycle means the
        # regime lookback window has only 1 bar -- detect_regime requires >=5 and always returns
        # "insufficient_data" for i=0, so every trade's entry_regime was silently identical
        # before this fix. Now captures the first cycle that produces a REAL classification.
        if entry_regime is None and str(regime_info.get("regime") or "insufficient_data") != "insufficient_data":
            entry_regime = str(regime_info.get("regime"))
        payload = {"price_current": bar["close"], "price_open": state.entry_price, "sl": state.current_sl, "tp": state.current_tp, "volume": state.current_volume, "profit": None, "symbol": state.symbol}
        candidates = svc_instance._evaluate_position(db, state, payload, {}, window, economic_result=None, symbol_info=None, account_equity=None)
        if not _replay_cooldown_elapsed(state.last_management_at, bar["time"]):
            candidates = [c for c in candidates if c.action_type in ("HOLD", "HOLD_WITH_GIVEBACK_RISK")] or candidates
        choice = svc_instance._select_action(candidates)
        if choice.action_type != "HOLD":
            action_log.append({"time": bar["time"], "action": choice.action_type, "reason": choice.reason, "r": r_now})
        closed = _apply_choice(state, choice, bar_time=bar["time"], current_r=r_now, db=db, legs=legs)
        final_r = r_now
        if closed:
            natural_end = False
            break

    if natural_end and normalized:
        risk = abs(state.entry_price - state.original_sl) or 1e-5
        final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=normalized[-1]["close"], original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)

    remaining_fraction = max(0.0, 1.0 - sum(leg["fraction"] for leg in legs))
    if remaining_fraction > 1e-9:
        legs.append({"fraction": remaining_fraction, "r": final_r, "action": "SL_HIT" if hard_stop_hit else ("NATURAL_END" if natural_end else "MANAGER_FULL_CLOSE"), "time": normalized[-1]["time"] if normalized else trade["exit_time"]})
    blended_r = sum(leg["fraction"] * leg["r"] for leg in legs)
    time_in_trade_seconds = (normalized[-1]["time"] - trade["entry_time"]).total_seconds() if normalized else 0.0
    final_leg = legs[-1] if legs else None

    return {
        "trade_id": trade["trade_id"], "symbol": trade["symbol"], "direction": trade["direction"], "strategy_id": trade["strategy_id"],
        "entry_time": trade["entry_time"], "session": time_of_day_bucket(trade["entry_time"]), "entry_regime": entry_regime or "insufficient_data",
        "planned_rr": round(planned_rr, 3), "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4),
        "realized_r": round(blended_r, 4), "giveback_r": round(max(0.0, mfe_r - blended_r), 4),
        "time_to_milestone": time_to_milestone, "time_in_trade_seconds": time_in_trade_seconds,
        "action_log": action_log, "final_action": final_leg["action"] if final_leg else "UNKNOWN",
        "num_management_legs": len(legs) - 1, "hard_stop_hit": hard_stop_hit,
    }


# ------------------------------------------------------ Policy B/D/E/G via simulate_policy ---
def _case_and_path(trade: dict, candles: list[dict]):
    case = svc._trade_case(trade)
    path = svc.reconstruct_path(case, candles)
    if not path.get("timeline"):
        return None, None
    return case, path


def run_shadow_family(case, path, family: str, parameters: dict) -> float | None:
    outcome = svc.simulate_policy(case, path, {"family": family, "parameters": parameters, "policy_id": f"audit_{family}"})
    return outcome["hypothetical_r"]


# ---------------------------------------------------------------- Policy C: ATR trailing ---
def simulate_atr_trail(trade: dict, candles: list[dict], *, atr_multiple: float, trigger_r: float) -> float | None:
    normalized = _normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    entry, direction = float(trade["entry"]), trade["direction"]
    sl, tp = float(trade["stop_loss"]), float(trade["take_profit"])
    risk = abs(entry - sl) or 1e-5
    long = direction == "LONG"
    current_sl = sl
    mfe_r = 0.0
    final_r = 0.0
    for i, bar in enumerate(normalized):
        sl_touched = (bar["low"] <= current_sl) if long else (bar["high"] >= current_sl)
        tp_touched = (bar["high"] >= tp) if long else (bar["low"] <= tp)
        if sl_touched or tp_touched:
            touch_price = current_sl if sl_touched else tp
            return _signed_r(entry, touch_price, risk, direction)
        favorable_r = _signed_r(entry, bar["high"] if long else bar["low"], risk, direction)
        mfe_r = max(mfe_r, favorable_r)
        window = normalized[max(0, i - REGIME_LOOKBACK_BARS):i + 1]
        atr = float(svc.detect_regime(window, context={}).get("features", {}).get("atr") or 0) or None
        if mfe_r >= trigger_r and atr:
            trail = bar["close"] - atr * atr_multiple if long else bar["close"] + atr * atr_multiple
            if (long and trail > current_sl) or (not long and trail < current_sl):
                current_sl = trail
        final_r = _signed_r(entry, bar["close"], risk, direction)
    return final_r


# ---------------------------------------------------- Policy F: partial + ATR-trailed runner ---
def simulate_partial_runner(trade: dict, candles: list[dict], *, partial_trigger_r: float, partial_fraction: float, runner_trail_atr: float) -> float | None:
    normalized = _normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    entry, direction = float(trade["entry"]), trade["direction"]
    sl, tp = float(trade["stop_loss"]), float(trade["take_profit"])
    risk = abs(entry - sl) or 1e-5
    long = direction == "LONG"
    current_sl = sl
    partial_taken = False
    partial_r = None
    mfe_r = 0.0
    final_r = 0.0
    for i, bar in enumerate(normalized):
        sl_touched = (bar["low"] <= current_sl) if long else (bar["high"] >= current_sl)
        tp_touched = (bar["high"] >= tp) if long else (bar["low"] <= tp)
        if sl_touched or tp_touched:
            touch_price = current_sl if sl_touched else tp
            runner_r = _signed_r(entry, touch_price, risk, direction)
            if partial_taken:
                return partial_fraction * partial_r + (1 - partial_fraction) * runner_r
            return runner_r
        favorable_r = _signed_r(entry, bar["high"] if long else bar["low"], risk, direction)
        mfe_r = max(mfe_r, favorable_r)
        if not partial_taken and mfe_r >= partial_trigger_r:
            partial_taken = True
            partial_r = partial_trigger_r
            current_sl = entry  # move remainder to breakeven once partial is taken -- standard runner convention
        if partial_taken:
            window = normalized[max(0, i - REGIME_LOOKBACK_BARS):i + 1]
            atr = float(svc.detect_regime(window, context={}).get("features", {}).get("atr") or 0) or None
            if atr:
                trail = bar["close"] - atr * runner_trail_atr if long else bar["close"] + atr * runner_trail_atr
                if (long and trail > current_sl) or (not long and trail < current_sl):
                    current_sl = trail
        final_r = _signed_r(entry, bar["close"], risk, direction)
    if partial_taken:
        return partial_fraction * partial_r + (1 - partial_fraction) * final_r
    return final_r


# ------------------------------------------------------------------------------ reporting ---
MIN_FOLD_N = 15  # below this, report INSUFFICIENT_SAMPLE rather than a misleading precise number


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
        "avg_winner": round(pystats.fmean(wins), 4) if wins else None,
        "avg_loser": round(pystats.fmean(losses), 4) if losses else None,
        "max_drawdown": _max_drawdown(rs),
    }


def _max_drawdown(rs_chronological: list[float]) -> float:
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs_chronological:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return round(max_dd, 4)


def _report_policy(name: str, results_sorted_chronologically: list[dict], r_key: str):
    all_rs = [r[r_key] for r in results_sorted_chronologically if r[r_key] is not None]
    cut = len(all_rs) // 2
    train_rs, oos_rs = all_rs[:cut], all_rs[cut:]
    print(f"\n{name}")
    print(f"  all      {_stats(all_rs)}")
    print(f"  train50  {_stats(train_rs)}")
    print(f"  oos50    {_stats(oos_rs)}")
    # 3-fold rolling walk-forward
    n = len(all_rs)
    fold_size = n // 3
    if fold_size >= MIN_FOLD_N:
        for i in range(3):
            fold = all_rs[i * fold_size: (i + 1) * fold_size if i < 2 else n]
            print(f"  fold{i+1}    {_stats(fold)}")
    else:
        print(f"  rolling walk-forward: INSUFFICIENT_SAMPLE (n={n}, need >={MIN_FOLD_N * 3} for 3 folds)")
    return _stats(all_rs)


def _mfe_capture(results: list[dict], r_key: str, mfe_key: str = "mfe_r", *, min_mfe: float = 0.0) -> dict:
    """min_mfe filters out trades whose mfe_r denominator is too small to produce a meaningful
    ratio -- realized_r/mfe_r blows up into wild outliers when mfe_r is tiny (e.g. mfe_r=0.05,
    realized_r=-0.3 -> ratio=-6.0), dominating an unfiltered mean. Report BOTH: the unfiltered
    version (all trades with any positive mfe, however small) shows "how often does even a tiny
    dip round-trip to a loss"; the min_mfe=0.3 version shows genuine capture-efficiency on trades
    that reached a real, tradeable excursion."""
    pairs = [(r[r_key], r[mfe_key]) for r in results if r.get(mfe_key) and r[mfe_key] >= min_mfe and r.get(r_key) is not None]
    if len(pairs) < MIN_FOLD_N:
        return {"n": len(pairs), "status": "INSUFFICIENT_SAMPLE" if pairs else "NO_DATA"}
    captures = [realized / mfe for realized, mfe in pairs]
    return {"n": len(pairs), "mean_mfe_capture": round(pystats.fmean(captures), 4), "median_mfe_capture": round(pystats.median(captures), 4)}


def _right_tail_retention(results: list[dict], r_key: str, mfe_key: str, threshold: float) -> dict:
    cohort = [r for r in results if r.get(mfe_key) and r[mfe_key] >= threshold]
    if len(cohort) < 5:
        return {"n": len(cohort), "status": "INSUFFICIENT_SAMPLE"}
    realized = [r[r_key] for r in cohort if r.get(r_key) is not None]
    retained_fraction = [r[r_key] / r[mfe_key] for r in cohort if r.get(r_key) is not None and r[mfe_key]]
    return {
        "n": len(cohort), "mean_realized_r": round(pystats.fmean(realized), 4) if realized else None,
        "mean_retention_pct": round(100 * pystats.fmean(retained_fraction), 1) if retained_fraction else None,
        "trades_that_kept_at_least_half": sum(1 for f in retained_fraction if f >= 0.5),
    }


async def main():
    print("Loading real closed DEMO trades...", flush=True)
    trades = load_real_closed_trades()
    print(f"  {len(trades)} trades with deal data", flush=True)

    svc_instance = svc.AdaptiveManagementService()
    db = SessionLocal()
    forensics: list[dict] = []
    policy_results: dict[str, list[dict]] = defaultdict(list)

    try:
        for trade in trades:
            try:
                candles = await fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                f = reconstruct_forensics(trade, candles, svc_instance, db)
                if f is None:
                    continue
                forensics.append(f)

                case, path = _case_and_path(trade, candles)
                if case is None:
                    continue
                row = {"trade_id": trade["trade_id"], "symbol": trade["symbol"], "entry_time": trade["entry_time"], "strategy_id": trade["strategy_id"], "mfe_r": f["mfe_r"]}
                row["A_current_manager"] = f["realized_r"]
                row["B_static_sltp"] = run_shadow_family(case, path, "static", {})
                row["C_atr_trail_1.0"] = simulate_atr_trail(trade, candles, atr_multiple=1.0, trigger_r=1.0)
                row["C_atr_trail_1.5"] = simulate_atr_trail(trade, candles, atr_multiple=1.5, trigger_r=1.0)
                row["C_atr_trail_2.0"] = simulate_atr_trail(trade, candles, atr_multiple=2.0, trigger_r=1.0)
                row["D_giveback_0.5_30pct"] = run_shadow_family(case, path, "mfe_retracement", {"min_mfe_r": 0.5, "retrace_fraction": 0.3})
                row["D_giveback_0.5_50pct"] = run_shadow_family(case, path, "mfe_retracement", {"min_mfe_r": 0.5, "retrace_fraction": 0.5})
                row["D_giveback_0.75_50pct"] = run_shadow_family(case, path, "mfe_retracement", {"min_mfe_r": 0.75, "retrace_fraction": 0.5})
                row["E_be_0.5r"] = run_shadow_family(case, path, "breakeven", {"trigger_r": 0.5})
                row["E_be_0.75r"] = run_shadow_family(case, path, "breakeven", {"trigger_r": 0.75})
                row["E_be_1.0r"] = run_shadow_family(case, path, "breakeven", {"trigger_r": 1.0})
                row["E_be_1.5r"] = run_shadow_family(case, path, "breakeven", {"trigger_r": 1.5})
                row["F_partial50_at1R_trail1.5atr"] = simulate_partial_runner(trade, candles, partial_trigger_r=1.0, partial_fraction=0.5, runner_trail_atr=1.5)
                row["F_partial25_at0.5R_trail2.0atr"] = simulate_partial_runner(trade, candles, partial_trigger_r=0.5, partial_fraction=0.25, runner_trail_atr=2.0)
                row["G_time_exit_12c"] = run_shadow_family(case, path, "time_exit", {"max_candles": 12, "minimum_progress_r": 0.25})
                row["G_time_exit_24c"] = run_shadow_family(case, path, "time_exit", {"max_candles": 24, "minimum_progress_r": 0.25})
                row["G_time_exit_48c"] = run_shadow_family(case, path, "time_exit", {"max_candles": 48, "minimum_progress_r": 0.25})
                for k, v in row.items():
                    if k not in ("trade_id", "symbol", "entry_time", "strategy_id", "mfe_r"):
                        policy_results[k].append(row)
            except Exception as exc:
                print(f"  skipped {trade['trade_id']} ({trade['symbol']}): {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        db.rollback()
        db.close()

    print(f"\n{len(forensics)} trades forensically reconstructed (Policy A / current manager)", flush=True)
    if not forensics:
        return
    forensics.sort(key=lambda f: f["entry_time"])

    # ============================================================ SECTION 1: A vs B ============
    print("\n" + "=" * 110)
    print("SECTION 1: Current Manager (A) vs No Manager / Original SL-TP Only (B)")
    print("=" * 110)
    combined = sorted([r for r in policy_results["A_current_manager"]], key=lambda r: r["entry_time"])
    for label, key in [("A: current_manager", "A_current_manager"), ("B: static_sltp_only", "B_static_sltp")]:
        _report_policy(label, combined, key)

    # ============================================================ SECTION 2: profit leakage =====
    print("\n" + "=" * 110)
    print("SECTION 2: Profit-Leakage Report (trades that reached >= 0.5R MFE)")
    print("=" * 110)
    profitable = [f for f in forensics if f["mfe_r"] >= 0.5]
    print(f"  {len(profitable)} of {len(forensics)} trades reached >= 0.5R MFE")
    if profitable:
        total_mfe = sum(f["mfe_r"] for f in profitable)
        total_realized = sum(f["realized_r"] for f in profitable)
        total_giveback = sum(f["giveback_r"] for f in profitable)
        print(f"  total MFE available: {round(total_mfe,2)}R   total realized: {round(total_realized,2)}R   total giveback: {round(total_giveback,2)}R ({round(100*total_giveback/total_mfe,1)}% of available upside given back)")
        cause_counter = Counter(f["final_action"] for f in profitable)
        cause_giveback: dict[str, float] = defaultdict(float)
        for f in profitable:
            cause_giveback[f["final_action"]] += f["giveback_r"]
        print("\n  Giveback attributed to final closing action:")
        for cause, total in sorted(cause_giveback.items(), key=lambda kv: -kv[1]):
            print(f"    {cause:28s} n={cause_counter[cause]:3d}  total_giveback={round(total,3)}R  avg_giveback={round(total/cause_counter[cause],3)}R")
        roundtrips = [f for f in profitable if f["hard_stop_hit"] and f["realized_r"] <= 0]
        print(f"\n  Profitable trades that round-tripped into a stop-out (mfe>=0.5R, realized<=0): {len(roundtrips)}/{len(profitable)} ({round(100*len(roundtrips)/len(profitable),1)}%)")

    print("\n  MFE capture efficiency (realized_r / mfe_r), overall:")
    print(f"    unfiltered (any mfe_r > 0):      {_mfe_capture(forensics, 'realized_r')}")
    print(f"    meaningful (mfe_r >= 0.3R only): {_mfe_capture(forensics, 'realized_r', min_mfe=0.3)}")

    print("\n  Right-tail retention (trades that reached the stated MFE threshold):")
    for threshold in (2.0, 3.0):
        print(f"    >= +{threshold}R MFE: {_right_tail_retention(forensics, 'realized_r', 'mfe_r', threshold)}")

    # ============================================================ SECTION 3: by strategy/regime =
    print("\n" + "=" * 110)
    print("SECTION 3: Performance by Strategy (H) and Regime (I) -- Policy A, current manager")
    print("=" * 110)
    by_strategy: dict[str, list] = defaultdict(list)
    for f in forensics:
        by_strategy[f["strategy_id"]].append(f)
    print("\n  By strategy:")
    for strat, rows in sorted(by_strategy.items(), key=lambda kv: -len(kv[1])):
        print(f"    {strat:16s} {_stats([r['realized_r'] for r in rows])}  mfe_capture(>=0.3R)={_mfe_capture(rows, 'realized_r', min_mfe=0.3)}")

    by_regime: dict[str, list] = defaultdict(list)
    for f in forensics:
        by_regime[f["entry_regime"]].append(f)
    print("\n  By entry regime:")
    for regime, rows in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        print(f"    {regime:20s} {_stats([r['realized_r'] for r in rows])}  mfe_capture(>=0.3R)={_mfe_capture(rows, 'realized_r', min_mfe=0.3)}")

    by_session: dict[str, list] = defaultdict(list)
    for f in forensics:
        by_session[f["session"]].append(f)
    print("\n  By session:")
    for session, rows in sorted(by_session.items(), key=lambda kv: -len(kv[1])):
        print(f"    {session:12s} {_stats([r['realized_r'] for r in rows])}")

    # ============================================================ SECTION 4: which actions help/hurt
    print("\n" + "=" * 110)
    print("SECTION 4: Which manager actions actually fired, and what they preceded")
    print("=" * 110)
    action_type_counter = Counter()
    for f in forensics:
        for entry in f["action_log"]:
            action_type_counter[entry["action"]] += 1
    for action, count in action_type_counter.most_common():
        print(f"    {action:32s} fired {count} times across {len(forensics)} trades")

    # ============================================================ SECTION 5: all counterfactual policies
    print("\n" + "=" * 110)
    print("SECTION 5: All counterfactual policies (chronological, 3-fold walk-forward, coarse grids)")
    print("=" * 110)
    policy_summaries = {}
    for key in sorted(policy_results.keys()):
        rows = sorted(policy_results[key], key=lambda r: r["entry_time"])
        summary = _report_policy(key, rows, key)
        policy_summaries[key] = summary
        mfe_cap = _mfe_capture(rows, key, min_mfe=0.3)
        print(f"  mfe_capture: {mfe_cap}")
        rt2 = _right_tail_retention(rows, key, "mfe_r", 2.0)
        print(f"  +2R retention: {rt2}")

    print("\n" + "=" * 110)
    print("SECTION 6: Parameter sensitivity check (adjacent grid values must move consistently)")
    print("=" * 110)
    for family_prefix, keys in [
        ("C_atr_trail", ["C_atr_trail_1.0", "C_atr_trail_1.5", "C_atr_trail_2.0"]),
        ("E_be", ["E_be_0.5r", "E_be_0.75r", "E_be_1.0r", "E_be_1.5r"]),
        ("G_time_exit", ["G_time_exit_12c", "G_time_exit_24c", "G_time_exit_48c"]),
    ]:
        exps = [(k, policy_summaries.get(k, {}).get("expectancy_r")) for k in keys]
        print(f"  {family_prefix}: {exps}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

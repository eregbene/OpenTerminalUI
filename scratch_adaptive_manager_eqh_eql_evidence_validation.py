"""Adaptive Manager EQH/EQL validation, v2 -- CORRECTED per explicit clarification: EQH/EQL must
be evaluated as ADDITIONAL EVIDENCE inside the REAL, full current Adaptive Manager decision engine
(service.py::_evaluate_position -- the actual live HOLD/BE/TRAIL/PARTIAL/PROTECT/EXIT candidate
generator, priority-ranked and reduced via _select_action), never as a replacement/independent
policy. The prior scratch_adaptive_manager_eqh_eql_validation.py compared against a single
isolated shadow family (trailing_atr_1_5_v1) -- this version replays the REAL decision function.

WHY A NEW STATE-MACHINE REPLAY WAS NECESSARY: _evaluate_position depends on a rich, INCREMENTALLY
UPDATED state object (max_achieved_r, tp_progress, winner_classification, partial_profit_stage,
current_sl/tp, ...) that in live operation is maintained by _sync_position_state every real
monitoring cycle from broker payloads. There is no existing point-in-time replay of that state
machine. This script reuses the REAL _evaluate_position/_select_action/tp_protection.* functions
UNCHANGED, and re-derives ONLY the (much simpler) state-update arithmetic _sync_position_state
performs, using the SAME formulas, driven bar-by-bar from real historical M15 candles instead of
live broker payloads.

EXPLICIT SCOPE LIMITATIONS (stated, not hidden):
  - economic_result=None, context={} -- no historical Forex Factory calendar-guard state exists
    for backtesting; ECONOMIC_REDUCE_SIZE/ECONOMIC_MANAGE_EXISTING_ONLY/EVENT_RISK_REDUCTION never
    fire in this replay. Out of scope for what's being tested (EQH/EQL), not a claim they don't
    matter live.
  - account_equity=None -- HOLD_WITH_GIVEBACK_RISK (equity-scaled layer) never fires; that layer
    is priority 90 (weaker than every real protective candidate) and orthogonal to EQH/EQL.
  - v2_mode candidates excluded -- kept the comparison to the core, well-understood decision
    layer this session has direct visibility into.
  - R uses price-distance only (original_risk_money=None) -- no historical per-trade broker
    risk-money snapshot exists to replay; this matches _compute_r's own documented fallback path.
  - _cooldown_elapsed (real function) compares against wall-clock utcnow(), incompatible with
    historical replay by construction -- reimplemented locally against the REPLAY's own bar time,
    same env-configured duration (ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS).
  - _stage_already_executed reads a real DB table -- this script uses a REAL SessionLocal()
    session so that table's gating logic works correctly ACROSS cycles within one trade's replay
    (insert-then-flush, never committed), but NEVER commits, so nothing persists.

EQH/EQL is wired in as a MODULATOR, never a generator (explicit instruction: "rather than
independently issuing those actions"; "never cancel an existing hard safety/risk action"):
  - Only ever SUPPRESSES (never invents) exactly the three DISCRETIONARY candidates the real
    engine can propose: MFE_PROTECTION_CLOSE(30), TP_PROGRESS_PARTIAL_PROTECT(35), TRAIL_STOP(60)
    -- priority tiers strictly softer than every hard-safety action (THESIS_INVALIDATION_CLOSE=10,
    EVENT_RISK_REDUCTION=20, ECONOMIC_REDUCE_SIZE=21, TP_PROGRESS_PROFIT_LOCK=25), which this
    script never touches.
  - Suppression fires ONLY when an active (unswept) EQH/EQL pool on the trade's "ahead" side
    (EQH/buy_side for LONG, EQL/sell_side for SHORT) still lies beyond current price, AND no
    recent (~90min) sweep-and-rejection exists on EITHER the ahead side OR the opposing side
    (a rejection against the trade is itself risk/deterioration evidence -- see the user's own
    "EQH/EQL against the trade -> risk/deterioration evidence" requirement -- and must prevent
    suppression, reinforcing the existing candidate instead of weakening it).
  - When suppression does NOT apply (default), the real engine's own candidate stands unchanged
    -- this is the "reinforce" half: no new code path, it falls out of the conditional naturally.

Nothing here writes to any table (uncommitted session, rolled back at the end) or touches a real
position/broker. Real closed DEMO trades only, same point-in-time-safe M15 revision data as the
prior validation.
"""
from __future__ import annotations

import statistics as pystats
from datetime import datetime, timedelta

from backend.adaptive_management import service as svc
from backend.adaptive_management import tp_protection
from backend.adaptive_management.orm import AdaptivePartialExitStageORM, AdaptivePositionStateORM as PS, AdaptiveTradeEventORM as TE
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.historical_intelligence.orm import MT5CandleRevisionORM as REV
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import get_profile
from backend.market_structure.liquidity import detect_equal_levels, detect_liquidity_sweeps
from backend.market_structure.swings import detect_swings
from backend.shared.db import SessionLocal

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
RECENT_SWEEP_WINDOW = timedelta(hours=1, minutes=30)
SUPPRESSIBLE_ACTIONS = {"MFE_PROTECTION_CLOSE", "TP_PROGRESS_PARTIAL_PROTECT", "TRAIL_STOP"}
REGIME_LOOKBACK_BARS = 60  # bars of history fed to detect_regime/_swing_structure_level each cycle -- matches ADAPTIVE_MANAGEMENT_CANDLE_COUNT's live ballpark


class _State:
    __slots__ = (
        "position_id", "symbol", "direction", "entry_price", "opened_at",
        "original_sl", "original_tp", "current_sl", "current_tp",
        "original_volume", "current_volume",
        "max_achieved_r", "min_achieved_r", "tp_progress", "max_tp_progress",
        "winner_classification", "partial_profit_stage", "current_giveback_r", "last_management_at",
        "original_risk_money",
    )


def _replay_cooldown_elapsed(last_management_at: datetime | None, bar_time: datetime) -> bool:
    if not last_management_at:
        return True
    cooldown = svc._env_int("ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS", 120, minimum=10, maximum=3600)
    return (bar_time - last_management_at).total_seconds() >= cooldown


def load_equal_levels_for_symbol(symbol: str):
    with SessionLocal() as db:
        rows = db.query(REV).filter(REV.broker_symbol == symbol, REV.timeframe == "M15").order_by(REV.bar_timestamp_utc.asc(), REV.revision_number.asc()).all()
    if not rows:
        return None
    latest_by_bar = {}
    for r in rows:
        latest_by_bar[r.bar_timestamp_utc] = r
    ordered = sorted(latest_by_bar.values(), key=lambda r: r.bar_timestamp_utc)
    dict_rows = [{"time": r.bar_timestamp_utc, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.tick_volume} for r in ordered]
    bars = normalize_bars(dict_rows, symbol=symbol, timeframe="M15")
    config = get_profile("balanced")
    swings = detect_swings(bars, config, symbol=symbol, timeframe="M15")
    equal_levels = detect_equal_levels(bars, swings, config, symbol=symbol, timeframe="M15")
    equal_level_sweeps = detect_liquidity_sweeps(bars, equal_levels, config, symbol=symbol, timeframe="M15")
    return {"bars": bars, "equal_levels": equal_levels, "equal_level_sweeps": equal_level_sweeps}


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
                "trade_id": row.position_id, "symbol": row.symbol, "direction": row.direction, "volume": row.original_volume,
                "entry": row.entry_price, "stop_loss": row.original_sl, "take_profit": row.original_tp,
                "entry_time": row.opened_at, "exit_time": exit_deal.utc_time or row.closed_detected_at, "actual_pnl": realized_pnl,
            })
    return trades


async def _fetch_candles(symbol: str, entry_time, exit_time):
    return await svc._replay_candles(symbol, "M15", entry_time, exit_time)


def _active_unswept_ahead(equal_levels, equal_level_sweeps, *, side: str, at, current_price: float, direction: str) -> bool:
    swept_ids = {s.level_id for s in equal_level_sweeps if s.confirmation_time <= at}
    for lvl in equal_levels:
        if lvl.side != side or lvl.confirmation_time > at or lvl.id in swept_ids:
            continue
        level = float(lvl.level)
        if (direction == "LONG" and level > current_price) or (direction == "SHORT" and level < current_price):
            return True
    return False


def _recent_rejection_sweep(equal_level_sweeps, *, side: str, at) -> bool:
    return any(s.side == side and s.confirmation_time <= at and (at - s.confirmation_time) <= RECENT_SWEEP_WINDOW for s in equal_level_sweeps)


def _should_suppress(state: _State, equal_levels, equal_level_sweeps, *, at, current_price: float) -> bool:
    ahead_side = "buy_side" if state.direction == "LONG" else "sell_side"
    opposing_side = "sell_side" if state.direction == "LONG" else "buy_side"
    if _recent_rejection_sweep(equal_level_sweeps, side=ahead_side, at=at):
        return False  # the target itself just got rejected -- deterioration evidence, never suppress
    if _recent_rejection_sweep(equal_level_sweeps, side=opposing_side, at=at):
        return False  # a level against the trade just broke -- risk evidence, never suppress
    return _active_unswept_ahead(equal_levels, equal_level_sweeps, side=ahead_side, at=at, current_price=current_price, direction=state.direction)


def _new_state(trade: dict, *, variant: str) -> _State:
    s = _State()
    s.position_id = f"REPLAY_{variant}_" + trade["trade_id"]
    s.symbol = trade["symbol"]
    s.direction = trade["direction"].upper()
    s.entry_price = float(trade["entry"])
    s.opened_at = trade["entry_time"]
    s.original_sl = float(trade["stop_loss"])
    s.original_tp = float(trade["take_profit"])
    s.current_sl = s.original_sl
    s.current_tp = s.original_tp
    s.original_volume = float(trade["volume"] or 1.0)
    s.current_volume = s.original_volume
    s.max_achieved_r = 0.0
    s.min_achieved_r = 0.0
    s.tp_progress = 0.0
    s.max_tp_progress = 0.0
    s.winner_classification = "healthy_pullback"
    s.partial_profit_stage = "NONE"
    s.current_giveback_r = 0.0
    s.last_management_at = None
    s.original_risk_money = None
    return s


def _update_state_for_cycle(state: _State, *, current_price: float, candles_window: list[dict]) -> tuple[float, dict, float | None, float]:
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    r_now, _r_source = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=current_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
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
        "opposing_candles": svc._opposing_candles(candles_window, state.direction),
        "retracement_state": retracement_state, "regime": regime_info.get("regime", "insufficient_data"),
        "direction": state.direction, "candles_held": candles_held,
        "remaining_reward_r": (1.0 - progress) if progress is not None else None,
    })
    state.winner_classification = winner["classification"]
    return r_now, regime_info, atr, risk


def _apply_choice(state: _State, choice, *, bar_time, current_r: float, db, legs: list[tuple[float, float]]) -> bool:
    """Returns True if the position is now fully closed."""
    if choice.requested_sl is not None:
        state.current_sl = choice.requested_sl
    if choice.requested_tp is not None:
        state.current_tp = choice.requested_tp
    if choice.requested_volume and choice.requested_volume > 0 and state.current_volume > 0:
        fraction_of_original = min(1.0, choice.requested_volume / state.original_volume)
        legs.append((fraction_of_original, current_r))
        state.current_volume = max(0.0, state.current_volume - choice.requested_volume)
        if choice.action_type == "PARTIAL_PROFIT":
            state.partial_profit_stage = "PARTIAL_1_EXECUTED" if state.partial_profit_stage == "NONE" else "PARTIAL_2_EXECUTED"
        if choice.action_type == "TP_PROGRESS_PARTIAL_PROTECT":
            stage = (choice.evidence or {}).get("stage")
            if stage:
                db.add(AdaptivePartialExitStageORM(stage_id=f"REPLAY_{state.position_id}_{stage}", position_id=state.position_id, stage=stage))
                db.flush()
    if choice.action_type != "HOLD":
        state.last_management_at = bar_time
    return state.current_volume <= 1e-9


def simulate(trade: dict, candles: list[dict], svc_instance, db, *, equal_levels=None, equal_level_sweeps=None, use_eqh_eql: bool) -> dict | None:
    normalized = sorted((svc._normalize_candle(c) for c in candles), key=lambda c: c["time"] if c else datetime.min)
    normalized = [c for c in normalized if c and trade["entry_time"] <= c["time"] <= trade["exit_time"]]
    if len(normalized) < 3:
        return None
    state = _new_state(trade, variant="EQL" if use_eqh_eql else "BASE")
    legs: list[tuple[float, float]] = []
    final_r = 0.0
    hard_stop_hit = False

    for i, bar in enumerate(normalized):
        window = normalized[max(0, i - REGIME_LOOKBACK_BARS):i + 1]
        # Broker-side hard SL/TP touch check FIRST (unconditional -- the discretionary layer
        # never overrides a real stop/target fill).
        long = state.direction == "LONG"
        sl_touched = (bar["low"] <= state.current_sl) if long else (bar["high"] >= state.current_sl)
        tp_touched = (bar["high"] >= state.current_tp) if long else (bar["low"] <= state.current_tp)
        if sl_touched or tp_touched:
            risk = abs(state.entry_price - state.original_sl) or 1e-5
            touch_price = state.current_sl if sl_touched else state.current_tp
            final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=touch_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
            hard_stop_hit = True
            break

        r_now, regime_info, atr, risk = _update_state_for_cycle(state, current_price=bar["close"], candles_window=window)
        payload = {"price_current": bar["close"], "price_open": state.entry_price, "sl": state.current_sl, "tp": state.current_tp, "volume": state.current_volume, "profit": None, "symbol": state.symbol}
        candidates = svc_instance._evaluate_position(db, state, payload, {}, window, economic_result=None, symbol_info=None, account_equity=None)

        if use_eqh_eql and equal_levels is not None:
            suppress = _should_suppress(state, equal_levels, equal_level_sweeps, at=bar["time"], current_price=bar["close"])
            if suppress:
                candidates = [c for c in candidates if c.action_type not in SUPPRESSIBLE_ACTIONS]

        if not _replay_cooldown_elapsed(state.last_management_at, bar["time"]):
            candidates = [c for c in candidates if c.action_type in ("HOLD", "HOLD_WITH_GIVEBACK_RISK")] or candidates

        choice = svc_instance._select_action(candidates)
        closed = _apply_choice(state, choice, bar_time=bar["time"], current_r=r_now, db=db, legs=legs)
        final_r = r_now
        if closed:
            break

    remaining_fraction = max(0.0, 1.0 - sum(f for f, _ in legs))
    if remaining_fraction > 1e-9:
        legs.append((remaining_fraction, final_r))
    blended_r = sum(f * r for f, r in legs)
    risk_for_mfe = abs(state.entry_price - state.original_sl) or 1e-5
    long = state.direction == "LONG"

    def _signed_r(price: float) -> float:
        return (price - state.entry_price) / risk_for_mfe if long else (state.entry_price - price) / risk_for_mfe

    mfe_r = max((_signed_r(bar["high"] if long else bar["low"]) for bar in normalized), default=0.0)
    return {
        "trade_id": trade["trade_id"], "symbol": trade["symbol"], "entry_time": trade["entry_time"],
        "realized_r": round(blended_r, 4), "mfe_r": round(mfe_r, 4), "hard_stop_hit": hard_stop_hit,
        "num_management_legs": len(legs) - 1,  # excludes the final mark-to-exit leg
    }


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "win_rate": None, "profit_factor": None, "avg_winner": None, "avg_loser": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {
        "n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
        "avg_winner": round(pystats.fmean(wins), 4) if wins else None,
        "avg_loser": round(pystats.fmean(losses), 4) if losses else None,
    }


def _max_drawdown(rs_chronological: list[float]) -> float:
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs_chronological:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return round(max_dd, 4)


def _split_half(items):
    cut = len(items) // 2
    return items[:cut], items[cut:]


async def main():
    print("Loading real closed DEMO trades...", flush=True)
    trades = load_real_closed_trades()
    print(f"  {len(trades)} trades with deal data", flush=True)

    print("Loading EQH/EQL state per symbol (MT5 revision-tracked M15)...", flush=True)
    equal_levels_by_symbol = {}
    for sym in SYMBOLS:
        state = load_equal_levels_for_symbol(sym)
        if state:
            equal_levels_by_symbol[sym] = state
            print(f"  {sym}: {len(state['bars'])} bars, {len(state['equal_levels'])} EQH/EQL events, {len(state['equal_level_sweeps'])} sweeps", flush=True)

    svc_instance = svc.AdaptiveManagementService()
    db = SessionLocal()
    results = []
    try:
        for trade in trades:
            sym = trade["symbol"]
            if sym not in equal_levels_by_symbol:
                continue
            try:
                candles = await _fetch_candles(sym, trade["entry_time"], trade["exit_time"])
                if not candles:
                    continue
                state_info = equal_levels_by_symbol[sym]
                base = simulate(trade, candles, svc_instance, db, use_eqh_eql=False)
                eql = simulate(trade, candles, svc_instance, db, equal_levels=state_info["equal_levels"], equal_level_sweeps=state_info["equal_level_sweeps"], use_eqh_eql=True)
                if base is None or eql is None:
                    continue
                results.append({
                    "trade_id": trade["trade_id"], "symbol": sym, "entry_time": trade["entry_time"],
                    "base_r": base["realized_r"], "eql_r": eql["realized_r"],
                    "base_mfe": base["mfe_r"], "eql_mfe": eql["mfe_r"],
                    "base_legs": base["num_management_legs"], "eql_legs": eql["num_management_legs"],
                    "base_hard_stop": base["hard_stop_hit"], "eql_hard_stop": eql["hard_stop_hit"],
                    "action_changed": base["realized_r"] != eql["realized_r"] or base["num_management_legs"] != eql["num_management_legs"],
                })
            except Exception as exc:
                print(f"  skipped {trade['trade_id']} ({sym}): {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        db.rollback()  # never persist anything from this replay
        db.close()

    print(f"\n{len(results)} trades fully evaluated (current manager, unchanged) vs (current manager + EQH/EQL evidence)", flush=True)
    if not results:
        return
    results.sort(key=lambda r: r["entry_time"])

    print("\n" + "=" * 100)
    print("CURRENT MANAGER (unchanged) vs CURRENT MANAGER + EQH/EQL EVIDENCE -- chronological 50/50 split")
    print("=" * 100)
    for label, key in [("current_manager (baseline)", "base_r"), ("current_manager + EQH/EQL evidence", "eql_r")]:
        all_rs = [r[key] for r in results]
        h1, h2 = _split_half(results)
        h1_rs, h2_rs = [r[key] for r in h1], [r[key] for r in h2]
        print(f"\n{label}:")
        print(f"  all      {_stats(all_rs)}  max_drawdown={_max_drawdown(all_rs)}")
        print(f"  1st-half {_stats(h1_rs)}  max_drawdown={_max_drawdown(h1_rs)}")
        print(f"  2nd-half {_stats(h2_rs)}  max_drawdown={_max_drawdown(h2_rs)}")

    print("\n--- MFE capture, premature-exit rate (realized_r < mfe_r - 0.1 while NOT a hard stop), stop-out rate ---")
    for label, r_key, mfe_key, stop_key in [("current_manager", "base_r", "base_mfe", "base_hard_stop"), ("current_manager + EQH/EQL", "eql_r", "eql_mfe", "eql_hard_stop")]:
        mfe_captures = [(r[r_key] / r[mfe_key]) for r in results if r[mfe_key] and r[mfe_key] > 0]
        premature = sum(1 for r in results if not r[stop_key] and r[mfe_key] > 0.3 and r[r_key] < r[mfe_key] - 0.1)
        stopped = sum(1 for r in results if r[stop_key])
        print(f"  {label:26s} mean_mfe_capture={round(pystats.fmean(mfe_captures),4) if mfe_captures else None}  premature_exit_rate={round(premature/len(results),4)}  stop_out_rate={round(stopped/len(results),4)}")

    changed = sum(1 for r in results if r["action_changed"])
    print(f"\nEQH/EQL evidence changed the manager's outcome on {changed}/{len(results)} trades ({round(100*changed/len(results),1)}%)")

    print("\n--- By symbol (current_manager vs current_manager + EQH/EQL) ---")
    by_symbol: dict[str, list] = {}
    for r in results:
        by_symbol.setdefault(r["symbol"], []).append(r)
    for sym, rows in sorted(by_symbol.items()):
        print(f"  {sym:8s} n={len(rows):3d}  baseline={_stats([r['base_r'] for r in rows])}  +eqh_eql={_stats([r['eql_r'] for r in rows])}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

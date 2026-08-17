"""Adaptive Manager EQH/EQL validation -- SEPARATE from entry-decision testing and Historical
Intelligence. Uses the EXISTING, tested position-path-replay/policy-simulation infrastructure
(adaptive_management.service: _replay_candles, reconstruct_path, simulate_policy, default_
policies, TradeCase) against REAL closed DEMO trades (AdaptivePositionStateORM, 451 trades across
all 10 symbols, 2026-08-07 to 2026-08-17) -- never a live broker call, never a synthetic path.

Baselines (both EXISTING, unmodified policy families -- never redefined here):
  - static_baseline_v1: original SL/TP, no intervention.
  - trailing_atr_1_5_v1: the existing trailing-exit family (trigger_r=1.0, trail_r=0.6) -- the
    real management-style baseline the EQH/EQL policy is built ON TOP OF, so the comparison
    isolates the EQH/EQL layer's own incremental effect rather than comparing against a straw man.

New family (equal_levels_v1, THIS script only -- not committed to production service.py; that
only happens if the evidence here supports it): identical trailing_atr_1_5 trigger, with two
EQH/EQL overrides, matching the user's exact intent (never mechanical on every touch/sweep):
  (a) HOLD LONGER: when the trailing trigger fires, if an ACTIVE (unswept) EQH/EQL pool of the
      trade's own "ahead" side (EQH/buy_side for LONG, EQL/sell_side for SHORT) still lies beyond
      current price in the favorable direction, suppress this exit and keep scanning -- there is
      still a plausible liquidity target to run toward.
  (b) PROTECT ON REJECTION: independently of (a)'s trigger, if a sweep of that same "ahead" side
      pool occurred within the last ~90 minutes (recent_window) -- note a LiquiditySweep object
      by construction already means "breached AND reclaimed", i.e. genuinely rejected, never a
      bare touch -- exit there. This is real structural failure evidence, not a touch-triggered
      rule.
  Falls back to the exact trailing_atr_1_5 exit when neither condition applies.

Point-in-time safety: EQH/EQL pool/sweep state is computed identically to every other EQH/EQL
validation this session (detect_swings -> detect_equal_levels -> detect_liquidity_sweeps, all
internally causal/confirmation-delayed) over each symbol's full MT5-provider M15 history
(mt5_candle_revisions, latest revision per bar -- UTC-correct via bar_timestamp_utc, sidesteps
the MT5-provider canonical-table timestamp_utc backfill gap found while building this).

Metrics: realized R (hypothetical_r), expectancy, PF, MFE capture, profit giveback (mfe -
exit_r when positive), premature-exit rate, stop-out rate (exit_r <= -0.9), simple sequential
drawdown (peak-to-trough of cumulative R in chronological order), % of trades where the action
differs from the trailing_atr_1_5 baseline. Chronological 50/50 split; same-sign-both-halves is
the bar, exactly like every other validation this session.

Nothing here writes to any table beyond what replay()/simulate_policy() already do when directly
invoked via this script's own calls into the tested service functions (research/counterfactual
tables only -- CounterfactualOutcomeORM/ShadowDecisionORM/TradePathSnapshotORM -- never touches
the live position, broker, or SL/TP).
"""
from __future__ import annotations

import asyncio
import statistics as pystats
from datetime import timedelta

from backend.adaptive_management import service as svc
from backend.adaptive_management.orm import AdaptivePositionStateORM as PS, AdaptiveTradeEventORM as TE
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.historical_intelligence.orm import MT5CandleRevisionORM as REV
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import get_profile
from backend.market_structure.liquidity import detect_equal_levels, detect_liquidity_sweeps
from backend.market_structure.swings import detect_swings
from backend.shared.db import SessionLocal

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
RECENT_SWEEP_WINDOW = timedelta(hours=1, minutes=30)
TRAIL_TRIGGER_R = 1.0
TRAIL_R = 0.6


def load_equal_levels_for_symbol(symbol: str):
    with SessionLocal() as db:
        rows = (
            db.query(REV)
            .filter(REV.broker_symbol == symbol, REV.timeframe == "M15")
            .order_by(REV.bar_timestamp_utc.asc(), REV.revision_number.asc())
            .all()
        )
    if not rows:
        return None
    latest_by_bar: dict = {}
    for r in rows:
        latest_by_bar[r.bar_timestamp_utc] = r  # ascending order -> last write wins -> latest revision
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
                "entry_time": row.opened_at, "exit_time": exit_deal.utc_time or row.closed_detected_at,
                "actual_pnl": realized_pnl, "strategy_id": row.strategy_id, "timeframe": row.timeframe,
            })
    return trades


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


def simulate_equal_levels_policy(case, path, equal_levels, equal_level_sweeps) -> dict:
    timeline = path.get("timeline") or []
    ahead_side = "buy_side" if case.direction == "LONG" else "sell_side"
    action, reason = "HOLD_TO_STATIC_EXIT", "baseline_static_sl_tp"
    exit_row = timeline[-1] if timeline else {"timestamp": (case.exit_time or case.entry_time).isoformat(), "r": 0}
    exit_r = float(exit_row.get("r") or 0)

    for row in timeline:
        t = svc._parse_dt(row["timestamp"])
        current_r = float(row.get("r") or 0)
        mfe_r = float(row.get("mfe_r") or 0)
        current_price = svc._price_from_r(case, current_r)

        if _recent_rejection_sweep(equal_level_sweeps, side=ahead_side, at=t):
            exit_row, exit_r = row, current_r
            action, reason = "EQUAL_LEVELS_PROTECT_SHADOW", "ahead_side_target_swept_and_rejected"
            break

        base_trigger = mfe_r >= TRAIL_TRIGGER_R and (mfe_r - current_r) >= TRAIL_R
        if base_trigger:
            if _active_unswept_ahead(equal_levels, equal_level_sweeps, side=ahead_side, at=t, current_price=current_price, direction=case.direction):
                continue  # hold longer -- a plausible liquidity target still lies ahead, unswept
            exit_row, exit_r = row, current_r
            action, reason = "EQUAL_LEVELS_TRAIL_EXIT_SHADOW", "trailing_threshold_hit_no_active_target"
            break

    hypothetical_pnl = svc._pnl_from_r(case, exit_r)
    actual_pnl = case.actual_pnl
    return svc.sanitize({
        "outcome_id": "OUTCOME_EQL_" + svc._hash({"trade": case.trade_id, "policy": "equal_levels_v1"})[:32],
        "trade_id": case.trade_id, "policy_id": "equal_levels_v1",
        "actual_pnl": actual_pnl, "hypothetical_pnl": hypothetical_pnl, "hypothetical_r": exit_r,
        "hypothetical_exit_time": exit_row["timestamp"], "hypothetical_exit_price": svc._price_from_r(case, exit_r),
        "difference_from_actual": hypothetical_pnl - actual_pnl,
        "mfe_capture": exit_r / path["mfe"] if path.get("mfe") else 0,
        "false_early_exit": actual_pnl > hypothetical_pnl and action != "HOLD_TO_STATIC_EXIT",
        "giveback_avoided": max(0.0, path.get("profit_retracement_from_mfe", 0) - max(0.0, path.get("mfe", 0) - exit_r)),
        "action": action, "reason": reason, "applicable": True,
    })


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "win_rate": None, "profit_factor": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
    return {"n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4), "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf}


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
        else:
            print(f"  {sym}: no MT5-provider M15 revision data -- skipped", flush=True)

    results = []
    for trade in trades:
        sym = trade["symbol"]
        if sym not in equal_levels_by_symbol:
            continue
        try:
            candles = await svc._replay_candles(sym, "M15", trade["entry_time"], trade["exit_time"])
            if not candles:
                continue
            case = svc._trade_case(trade)
            path = svc.reconstruct_path(case, candles)
            if not path.get("timeline"):
                continue
            static_outcome = svc.simulate_policy(case, path, {"family": "static", "parameters": {}, "policy_id": "static_baseline_v1"})
            trailing_outcome = svc.simulate_policy(case, path, {"family": "trailing", "parameters": {"trigger_r": TRAIL_TRIGGER_R, "trail_r": TRAIL_R}, "policy_id": "trailing_atr_1_5_v1"})
            state = equal_levels_by_symbol[sym]
            eql_outcome = simulate_equal_levels_policy(case, path, state["equal_levels"], state["equal_level_sweeps"])
            results.append({
                "trade_id": trade["trade_id"], "symbol": sym, "entry_time": trade["entry_time"],
                "static_r": static_outcome["hypothetical_r"], "trailing_r": trailing_outcome["hypothetical_r"], "eql_r": eql_outcome["hypothetical_r"],
                "static_mfe_capture": static_outcome["mfe_capture"], "trailing_mfe_capture": trailing_outcome["mfe_capture"], "eql_mfe_capture": eql_outcome["mfe_capture"],
                "trailing_false_early_exit": trailing_outcome["false_early_exit"], "eql_false_early_exit": eql_outcome["false_early_exit"],
                "action_changed": eql_outcome["action"] != trailing_outcome.get("action", "TRAILING_EXIT_SHADOW" if trailing_outcome["hypothetical_r"] != static_outcome["hypothetical_r"] else "HOLD_TO_STATIC_EXIT"),
                "eql_action": eql_outcome["action"], "path_mfe": path.get("mfe", 0),
            })
        except Exception as exc:
            print(f"  skipped {trade['trade_id']} ({sym}): {exc.__class__.__name__}: {exc}", flush=True)

    print(f"\n{len(results)} trades fully evaluated across baseline/trailing/equal_levels policies", flush=True)
    if not results:
        return
    results.sort(key=lambda r: r["entry_time"])

    print("\n" + "=" * 100)
    print("BASELINE vs TRAILING vs EQUAL_LEVELS -- chronological 50/50 split")
    print("=" * 100)
    for label, key in [("static_baseline_v1", "static_r"), ("trailing_atr_1_5_v1", "trailing_r"), ("equal_levels_v1 (NEW)", "eql_r")]:
        all_rs = [r[key] for r in results]
        h1, h2 = _split_half(results)
        h1_rs, h2_rs = [r[key] for r in h1], [r[key] for r in h2]
        print(f"\n{label}:")
        print(f"  all      {_stats(all_rs)}  max_drawdown={_max_drawdown(all_rs)}")
        print(f"  1st-half {_stats(h1_rs)}  max_drawdown={_max_drawdown(h1_rs)}")
        print(f"  2nd-half {_stats(h2_rs)}  max_drawdown={_max_drawdown(h2_rs)}")

    print("\n--- MFE capture, premature-exit rate, action-change rate ---")
    for label, mfe_key, exit_key in [("trailing_atr_1_5_v1", "trailing_mfe_capture", "trailing_false_early_exit"), ("equal_levels_v1", "eql_mfe_capture", "eql_false_early_exit")]:
        mfe_vals = [r[mfe_key] for r in results if r[mfe_key] is not None]
        premature = sum(1 for r in results if r[exit_key])
        print(f"  {label:24s} mean_mfe_capture={round(pystats.fmean(mfe_vals),4) if mfe_vals else None}  premature_exit_rate={round(premature/len(results),4)}")
    changed = sum(1 for r in results if r["action_changed"])
    print(f"\n  equal_levels_v1 action differs from trailing_atr_1_5_v1 baseline: {changed}/{len(results)} ({round(100*changed/len(results),1)}%)")
    from collections import Counter
    print(f"  equal_levels_v1 action distribution: {dict(Counter(r['eql_action'] for r in results))}")

    print("\n--- Stop-out rate (exit_r <= -0.9) ---")
    for label, key in [("static_baseline_v1", "static_r"), ("trailing_atr_1_5_v1", "trailing_r"), ("equal_levels_v1", "eql_r")]:
        stopped = sum(1 for r in results if r[key] <= -0.9)
        print(f"  {label:24s} {stopped}/{len(results)} ({round(100*stopped/len(results),1)}%)")

    print("\n--- By symbol (equal_levels_v1 vs trailing_atr_1_5_v1) ---")
    by_symbol: dict[str, list] = {}
    for r in results:
        by_symbol.setdefault(r["symbol"], []).append(r)
    for sym, rows in sorted(by_symbol.items()):
        trailing_rs = [r["trailing_r"] for r in rows]
        eql_rs = [r["eql_r"] for r in rows]
        print(f"  {sym:8s} n={len(rows):3d}  trailing={_stats(trailing_rs)}  equal_levels={_stats(eql_rs)}")


if __name__ == "__main__":
    asyncio.run(main())

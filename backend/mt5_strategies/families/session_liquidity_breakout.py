"""session_liquidity_breakout -- new strategy (2026-08-24), see docs/session-liquidity-breakout-
design-spec.md for the full design and docs/new-strategies-redundancy-audit.md for why this is
not a duplicate of the existing session_breakout.

Hypothesis: important session liquidity level -> sweep/break -> acceptance/displacement -> retest
of broken level -> rejection/continuation -> entry. session_breakout (families/_legacy.py) is a
bare close-beyond-level check with no displacement confirmation, no minimum break distance, no
retest, no rejection -- this strategy adds real, mandatory selectivity on the SAME level source
(ctx.m15_snapshot.session_levels, via backend.market_structure.sessions.detect_session_levels)
without stacking every SMC feature into one giant AND-chain: BOS/FVG-OB/premium-discount/EQH-EQL
are supporting evidence only, never mandatory.

Three entry-mode variants, computed from the SAME qualifying core break event so validation can
compare all of them without tripling the replay cost: IMMEDIATE (default, matches session_
breakout's own timing), RETEST, RETEST_REJECTION. Selected via MT5_SESSION_LIQUIDITY_BREAKOUT_
ENTRY_MODE for the live/registered evaluator; _evaluate_mode() is called directly with an
explicit mode by the validation harness to get all three from one context build.

SHADOW/DISABLED by default (backend/mt5_strategies/models.py) -- observation/fingerprint-
accumulation only until it clears its own 6-month point-in-time-safe validation.
"""
from __future__ import annotations

import os
from decimal import Decimal

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_float,
    _env_int,
    _eqh_eql_touch_count,
    _find_level_retest_hold,
    _geometry_metadata,
    _no_signal,
    _premium_discount_position,
    _recent_structure_break_against,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _structural_take_profit,
    _wick_rejection_score,
    _zone_overlap,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "session_liquidity_breakout"
_IMMEDIATE = "IMMEDIATE"
_RETEST = "RETEST"
_RETEST_REJECTION = "RETEST_REJECTION"


def _find_recent_level_break(rows: list[dict], level_price: float, direction: str, lookback: int) -> int | None:
    """Most recent bar where close crossed beyond `level_price` (the previous bar's close was
    still on the other side) -- session levels are fixed for the current snapshot, unlike a
    rolling Donchian channel, so this is a simple crossing scan, not a per-bar recomputation."""
    last_index = len(rows) - 1
    earliest = max(1, last_index - lookback)
    for idx in range(last_index, earliest - 1, -1):
        close = float(rows[idx]["close"])
        prev_close = float(rows[idx - 1]["close"])
        if direction == "LONG" and close > level_price and prev_close <= level_price:
            return idx
        if direction == "SHORT" and close < level_price and prev_close >= level_price:
            return idx
    return None


def _no_reclaim_since(rows: list[dict], break_bar_index: int, level_price: float, direction: str) -> bool:
    """True if no bar strictly after the break has closed back through the level -- filters the
    'swept and failed' case (what the engine's own LiquiditySweep detector would tag as a
    reversal, not a continuation) out of the 'acceptance' check."""
    for row in rows[break_bar_index + 1:]:
        close = float(row["close"])
        if direction == "LONG" and close <= level_price:
            return False
        if direction == "SHORT" and close >= level_price:
            return False
    return True


def _core_break_event(ctx: StrategyContext) -> dict | None:
    """The mandatory CORE, shared by all three entry-mode variants: a session/previous-day level
    (same source+filter session_breakout itself uses) closed-broken by a minimum ATR-normalized
    distance, confirmed by a co-located displacement, with no reclaim since. Returns None if any
    condition fails -- callers report the specific reason."""
    levels = ctx.m15_snapshot.session_levels
    if not levels:
        return {"reason": "no_session_levels"}
    rows = ctx.m15_rows
    price = float(_closes(rows).iloc[-1])
    atr = ctx.atr_m15
    if not atr or atr <= 0:
        return {"reason": "no_atr"}
    atr_f = float(atr)

    highs = [lv for lv in levels if lv.level_name in {"high", "previous_day_high"}]
    lows = [lv for lv in levels if lv.level_name in {"low", "previous_day_low"}]
    broken_high = max((lv for lv in highs if price > float(lv.level)), key=lambda lv: lv.level, default=None)
    broken_low = min((lv for lv in lows if price < float(lv.level)), key=lambda lv: lv.level, default=None)
    if broken_high is None and broken_low is None:
        return {"reason": "no_session_level_broken"}
    direction = "LONG" if broken_high is not None else "SHORT"
    reference = broken_high if broken_high is not None else broken_low
    level_price = float(reference.level)

    lookback = _env_int("MT5_SESSION_LIQUIDITY_BREAKOUT_LOOKBACK_BARS", 20)
    break_idx = _find_recent_level_break(rows, level_price, direction, lookback)
    if break_idx is None:
        return {"reason": "no_recent_level_crossing"}

    break_price = float(rows[break_idx]["close"])
    break_distance_atr = abs(break_price - level_price) / atr_f
    min_break_atr = _env_float("MT5_SESSION_LIQUIDITY_BREAKOUT_MIN_ATR", 0.3)
    if break_distance_atr < min_break_atr:
        return {"reason": "break_distance_below_minimum"}

    with_trend_direction = "bullish" if direction == "LONG" else "bearish"
    displaced = any(d.direction == with_trend_direction and abs(d.bar_index - break_idx) <= 2 for d in ctx.m15_snapshot.displacements)
    if not displaced:
        return {"reason": "no_displacement_confirmation"}

    if not _no_reclaim_since(rows, break_idx, level_price, direction):
        return {"reason": "reclaimed_since_break"}

    return {
        "direction": direction, "reference": reference, "level_price": level_price,
        "break_idx": break_idx, "break_distance_atr": break_distance_atr,
        "with_trend_direction": with_trend_direction, "atr": atr, "atr_f": atr_f,
    }


def _evaluate_mode(ctx: StrategyContext, mode: str) -> StrategySignal:
    core = _core_break_event(ctx)
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    if core is None or "reason" in core:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=(core or {}).get("reason", "no_core_event"))

    direction, reference, level_price, break_idx = core["direction"], core["reference"], core["level_price"], core["break_idx"]
    atr, atr_f = core["atr"], core["atr_f"]
    rows = ctx.m15_rows

    if mode == _IMMEDIATE:
        bars_since = (len(rows) - 1) - break_idx
        if bars_since > 5:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="breakout_too_old")
        setup_subtype = "liquidity_immediate"
        mode_evidence = {"bars_since_breakout": bars_since}
    else:
        hold = _find_level_retest_hold(ctx, level_bar_index=break_idx, broken_level=level_price, direction=direction)
        if hold is None:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_confirmed_retest_and_hold")
        confirm_idx, closest_price = hold
        bars_since_confirm = (len(rows) - 1) - confirm_idx
        if bars_since_confirm > 5:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="retest_confirm_too_old")
        wick_score = _wick_rejection_score(ctx, direction)
        if mode == _RETEST_REJECTION:
            min_wick = _env_float("MT5_SESSION_LIQUIDITY_BREAKOUT_MIN_WICK_REJECTION", 20.0)
            if wick_score < min_wick:
                return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_rejection")
            setup_subtype = "liquidity_retest_rejection"
        else:
            setup_subtype = "liquidity_retest"
        mode_evidence = {"retest_confirm_bar_index": confirm_idx, "bars_since_retest_confirm": bars_since_confirm,
                          "closest_retest_price": closest_price, "wick_rejection_score": wick_score}

    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, level_price, atr, min_atr_mult=1.0, max_atr_mult=3.0)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    structural = _structural_take_profit(ctx, direction=direction, entry=entry, stop=stop, atr=atr)
    if structural is not None:
        target, tp_basis = structural["tp1"], structural["basis"]
    else:
        target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
        tp_basis = "atr_flat_multiple"

    with_trend_direction = core["with_trend_direction"]
    bos_supporting = any(b.break_kind == "bos" and b.direction == with_trend_direction and b.bar_index >= break_idx - 3 for b in ctx.m15_snapshot.breaks)
    no_opposing = not _recent_structure_break_against(ctx, direction, lookback_bars=10)
    zone_overlap = _zone_overlap(ctx, zone_direction=with_trend_direction, price=float(entry))
    pd_position = _premium_discount_position(ctx)
    favorable_pd = pd_position is not None and ((pd_position <= 0.5) if direction == "LONG" else (pd_position >= 0.5))
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=float(entry), atr=atr_f)

    strength = 62.0
    strength += 8.0 if bos_supporting else 0.0
    strength += 6.0 if no_opposing else -10.0
    strength += 6.0 if zone_overlap else 0.0
    strength += 4.0 if favorable_pd else 0.0
    strength += 6.0 if eqh_eql_touches >= 2 else 0.0

    evidence = {
        "session": reference.session_name, "level_name": reference.level_name, "level": level_price,
        "break_distance_atr": round(core["break_distance_atr"], 3), "entry_mode": mode, "setup_subtype": setup_subtype,
        "bos_supporting": bos_supporting, "no_opposing_structure_break": no_opposing,
        "with_trend_zone_overlap": zone_overlap, "premium_discount_position": pd_position, "favorable_premium_discount_side": favorable_pd,
        "eqh_eql_touch_count": eqh_eql_touches, "htf_trend_h1": ctx.htf_trend_h1,
        "market_regime": ctx.market_regime, "take_profit_basis": tp_basis,
        **mode_evidence,
    }
    evidence.update(_squeeze_evidence(ctx))
    metadata = _geometry_metadata(ctx, entry, stop, level_price, atr, 1.0, 3.0)
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=max(50.0, min(100.0, strength)),
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


def evaluate_session_liquidity_breakout(ctx: StrategyContext) -> StrategySignal:
    mode = os.environ.get("MT5_SESSION_LIQUIDITY_BREAKOUT_ENTRY_MODE", _IMMEDIATE).strip().upper()
    return _evaluate_mode(ctx, mode)

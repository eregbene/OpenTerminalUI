"""support_resistance_bounce -- one of only two strategies (with smc_continuation) where the
EQH/EQL touch-count strength bonus is real, chronological-OOS-validated rather than merely
plausible (n=640, +0.12R train -> +0.24R OOS). Price within ATR-scaled tolerance of a
backend.market_structure LiquidityLevel, with a rejecting last candle.

2026-08-21 Phase 3 blueprint, Section 5: adds the one gap the audit found -- no HTF trend check at
all, meaning this strategy would happily fade a level directly into a strong HTF trend, the most
common way a bounce strategy loses. Reuses trend_pullback.py's exact inline HTF non-conflict
pattern (same field, same logic), not a new implementation. Gated behind
MT5_SR_BOUNCE_HTF_GATE_ENABLED (default False) -- with the flag off, behavior is identical to the
pre-Phase-3 implementation (still living in families/_legacy.py before this extraction).
"""
from __future__ import annotations

from decimal import Decimal

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_flag,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "support_resistance_bounce"


def evaluate_support_resistance_bounce(ctx: StrategyContext) -> StrategySignal:
    """Price within ATR-scaled tolerance of a backend.market_structure LiquidityLevel (swing-
    derived, not a separate rolling-window S/R calc), with a rejecting last candle."""
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    levels = ctx.m15_snapshot.liquidity_levels
    if not levels:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_liquidity_levels")
    price = float(_closes(ctx.m15_rows).iloc[-1])
    nearest = min(levels, key=lambda lv: abs(float(lv.level) - price))
    atr = float(ctx.atr_m15) if ctx.atr_m15 else 0.0001
    tolerance = max(float(nearest.tolerance), atr)
    if abs(float(nearest.level) - price) > tolerance:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="price_not_near_level")
    last = ctx.m15_rows[-1]
    bullish_reject = nearest.side == "sell_side" and float(last["close"]) > float(last["open"])
    bearish_reject = nearest.side == "buy_side" and float(last["close"]) < float(last["open"])
    if not (bullish_reject or bearish_reject):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_rejection_candle")
    direction = "LONG" if bullish_reject else "SHORT"

    # Phase 3 Section 5 (default off): same HTF non-conflict check trend_pullback.py already
    # applies to its own entries -- reused inline pattern, not a new helper, since it's a single
    # field comparison. A bounce whose direction conflicts with a strong HTF trend is the
    # textbook way this setup loses; this only rejects, it never boosts strength on agreement.
    if _env_flag("MT5_SR_BOUNCE_HTF_GATE_ENABLED", False):
        if direction == "LONG" and ctx.htf_trend_h1 == "bearish":
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="HTF_TREND_CONFLICT")
        if direction == "SHORT" and ctx.htf_trend_h1 == "bullish":
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="HTF_TREND_CONFLICT")

    entry = Decimal(str(price))
    atr_d = Decimal(str(atr))
    # Pure ATR stop, no structural level (entry is already essentially AT nearest.level by this
    # strategy's own trigger condition -- using it as the structural reference would collapse to
    # a near-zero raw distance every time, adding a spurious failure mode for no benefit since
    # min_atr_mult==max_atr_mult already fixes the distance regardless).
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr_d, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = entry + atr_d * Decimal("2.2") if direction == "LONG" else entry - atr_d * Decimal("2.2")
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=nearest.side, price=price, atr=atr)
    strength = 68.0 + (12.0 if eqh_eql_touches >= 2 else 0.0)
    evidence = {
        "level_id": nearest.id, "level": float(nearest.level), "side": nearest.side, "eqh_eql_touch_count": eqh_eql_touches,
        "htf_trend_h1": ctx.htf_trend_h1,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr_d, 1.2, 1.2))

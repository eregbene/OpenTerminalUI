"""ema_trend -- 1/10 symbols positive across the 8-year audit (-0.130R pooled), with a
catastrophic collapse in 2025-2026 (win rate down to 1.3%). EMA20/50/100 stack alignment +
positive/negative slope on M15.

2026-08-21 Phase 2 blueprint, Section 1: this strategy is one of three CHOP_RANGING disables it
targets directly -- trend-stack alignment is exactly the pattern that fires spuriously in ADX<20
chop (the audit's own diagnosis for the 2025-2026 collapse).

2026-08-21 Phase 3 blueprint, Section 4: the audit's real diagnosis goes deeper than regime --
this was the only strategy in the engine with ZERO market-structure involvement in its trigger,
using EMA stack alignment as a direct entry signal rather than a bias filter. Behind
MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED (default False), the EMA stack becomes bias-only and a
real structural trigger is required on top of it -- a with-bias M15 BOS, or a pullback-and-reclaim
of EMA20 confirmed by a reaction candle -- converging this strategy's entry logic toward
trend_pullback.py's, per the audit's own recommendation. Structural take-profit (Phase 3 Section
1) is also wired in behind MT5_STRUCTURAL_TP_ENABLED. Extracted out of families/_legacy.py into
its own module in Phase 2 to give these gates a real, reviewable home. With every flag at its
default (all False), this function remains behavior-identical to the pre-Phase-2 implementation.
"""
from __future__ import annotations

from decimal import Decimal

from backend.core.technicals import ema as _ema_series
from backend.market_structure.models import StructureBreakKind
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_flag,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _reaction_candle_confirms,
    _regime_atr_mult_scale,
    _regime_strategy_disabled,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _structural_take_profit,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "ema_trend"
_STRUCTURAL_TRIGGER_LOOKBACK_BARS = 5
_EMA20_RECLAIM_ATR_MULT = 0.5


def _structural_trigger_confirms(ctx: StrategyContext, direction: str, price: float, ema20_val: float, atr: float) -> bool:
    """MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED (Phase 3 Section 4): once the EMA stack has
    established BIAS (long_aligned/short_aligned in the caller), still require either (a) a
    recent with-bias BOS, or (b) price back at (a pullback-and-reclaim of) EMA20 -- the fastest
    of the three EMAs -- confirmed by a reaction candle. Either condition is real market-structure
    confirmation that a trend genuinely exists, not just that three moving averages happen to be
    numerically stacked in order."""
    with_trend = "bullish" if direction == "LONG" else "bearish"
    recent_window = max(0, len(ctx.m15_rows) - 1 - _STRUCTURAL_TRIGGER_LOOKBACK_BARS)
    recent_bos = any(
        b.break_kind == StructureBreakKind.BOS.value and b.direction == with_trend and b.bar_index >= recent_window
        for b in ctx.m15_snapshot.breaks
    )
    if recent_bos:
        return True
    near_ema20 = abs(price - ema20_val) <= atr * _EMA20_RECLAIM_ATR_MULT
    return near_ema20 and _reaction_candle_confirms(ctx, direction)


def evaluate_ema_trend(ctx: StrategyContext) -> StrategySignal:
    """EMA20/50/100 stack alignment + positive/negative slope on M15 as the BIAS (the
    intelligence/trading EMATrendStrategy pattern, EMA200 relaxed to EMA100 -- the standard
    100-bar M15 fetch used elsewhere in this pipeline doesn't reliably support a 200-period EMA).
    Behind MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED, bias alone is no longer sufficient -- see
    _structural_trigger_confirms."""
    if _regime_strategy_disabled(ctx, _STRATEGY_ID):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="REGIME_DISABLED_CHOP_RANGING")
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")

    closes = _closes(ctx.m15_rows)
    if len(closes) < 100:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    ema20, ema50, ema100 = _ema_series(closes, 20), _ema_series(closes, 50), _ema_series(closes, 100)
    price = float(closes.iloc[-1])
    slope20 = float(ema20.iloc[-1] - ema20.iloc[-6])
    long_aligned = price > ema20.iloc[-1] > ema50.iloc[-1] > ema100.iloc[-1] and slope20 > 0
    short_aligned = price < ema20.iloc[-1] < ema50.iloc[-1] < ema100.iloc[-1] and slope20 < 0
    if not (long_aligned or short_aligned):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="ema_stack_not_aligned")
    direction = "LONG" if long_aligned else "SHORT"
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal(str(abs(price - float(ema50.iloc[-1])) or 0.0001))

    if _env_flag("MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED", False):
        if not _structural_trigger_confirms(ctx, direction, price, float(ema20.iloc[-1]), float(atr)):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_STRUCTURAL_TRIGGER_CONFIRMATION")

    max_atr_mult = 1.5 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    # Phase 3 Section 1 (default off): structural target instead of a flat ATR multiple -- see
    # _shared.py's _structural_take_profit. Falls back to the original formula unchanged.
    structural = _structural_take_profit(ctx, direction=direction, entry=entry, stop=stop, atr=atr)
    if structural is not None:
        target = structural["tp1"]
        tp_basis = structural["basis"]
    else:
        target = entry + atr * Decimal("3.0") if direction == "LONG" else entry - atr * Decimal("3.0")
        tp_basis = "atr_flat_multiple"
    strength = min(100.0, 60.0 + abs(slope20) / max(float(atr), 1e-9) * 20.0)
    # EQH/EQL + squeeze: evidence-only for this strategy (not yet OOS-validated here specifically
    # -- see _eqh_eql_touch_count/_squeeze_evidence docstrings). Never adjusts strength.
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "ema100": float(ema100.iloc[-1]), "slope20": slope20,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
        "market_regime": ctx.market_regime,
        "structural_trigger_enabled": _env_flag("MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED", False),
        "take_profit_basis": tp_basis,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=strength,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, max_atr_mult))

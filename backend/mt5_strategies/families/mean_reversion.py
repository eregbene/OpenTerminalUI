"""mean_reversion -- the 8-year audit's top performer (+0.273R pooled, positive every year
2018-2026, most stable strategy in the whole engine). M15 RSI(14) extremes.

2026-08-21 Phase 2 blueprint, Section 1: TRENDING_STRONG disables this strategy (mean-reversion
against a strong trend is the audit's own textbook failure mode for every OTHER strategy that
doesn't already avoid it); CHOP_RANGING gives it a bounded strength boost, since chop is exactly
this strategy's best-case regime. Extracted out of families/_legacy.py into its own module for the
same reason ema_trend.py was -- these two regime gates need a real, reviewable home. With
MT5_REGIME_FILTER_ENABLED and MT5_SPREAD_FILTER_ENABLED at their defaults (both False), this
function is behavior-identical to the pre-Phase-2 implementation. Per the Phase 2 directive's own
non-negotiables, this strategy's core RSI trigger and stop/target geometry are UNTOUCHED -- it is
the audit's best performer and nothing here changes what makes it work.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from backend.core.technicals import rsi as _rsi_series
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _location_quality_mean_reversion,
    _no_signal,
    _regime_atr_mult_scale,
    _regime_strategy_disabled,
    _regime_strength_bonus,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _wick_rejection_score,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "mean_reversion"


def evaluate_mean_reversion(ctx: StrategyContext) -> StrategySignal:
    """M15 RSI(14) extremes. Regime suitability is enforced by the orchestrator's
    regime_compatible() gate (Part 5) and, behind its own flag, by Phase 2's ADX regime gate --
    neither is duplicated here."""
    if _regime_strategy_disabled(ctx, _STRATEGY_ID):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="REGIME_DISABLED_TRENDING_STRONG")
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")

    closes = _closes(ctx.m15_rows)
    if len(closes) < 30:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    rsi_series = _rsi_series(closes, 14)
    latest_rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else None
    if latest_rsi is None or (30 < latest_rsi < 70):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="rsi_not_extreme")
    direction = "LONG" if latest_rsi <= 30 else "SHORT"
    price = float(closes.iloc[-1])
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    max_atr_mult = 1.2 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.2, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("1.8") if direction == "LONG" else entry - atr * Decimal("1.8")
    strength = 55.0 + min(25.0, abs(50.0 - latest_rsi) - 20.0) + _regime_strength_bonus(ctx, _STRATEGY_ID)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "rsi14": latest_rsi, "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
        "market_regime": ctx.market_regime,
    }
    evidence.update(_squeeze_evidence(ctx))
    # Evidence-upgrade Steps 1-3 (docs/mean-reversion-trend-pullback-implementation-spec.md):
    # observability-only, does NOT feed `strength` above -- held to the same bar as
    # _eqh_eql_touch_count/displacement_magnitude_atr were before their own OOS validation.
    evidence["wick_rejection_score"] = _wick_rejection_score(ctx, direction)
    evidence.update(_location_quality_mean_reversion(ctx, direction=direction, price=price, atr=float(atr)))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=max(50.0, min(100.0, strength)),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.2, max_atr_mult))

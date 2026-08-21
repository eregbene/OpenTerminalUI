"""ema_trend -- 1/10 symbols positive across the 8-year audit (-0.130R pooled), with a
catastrophic collapse in 2025-2026 (win rate down to 1.3%). EMA20/50/100 stack alignment +
positive/negative slope on M15.

2026-08-21 Phase 2 blueprint, Section 1: this strategy is one of three CHOP_RANGING disables it
targets directly -- trend-stack alignment is exactly the pattern that fires spuriously in ADX<20
chop (the audit's own diagnosis for the 2025-2026 collapse). Extracted out of families/_legacy.py
(where it was a mechanical, unmodified carry-over from the pre-Phase-1 monolith) into its own
module specifically so this regime gate has a real, reviewable home rather than being buried in a
file explicitly documented as "not reproduced in this deliverable". With
MT5_REGIME_FILTER_ENABLED and MT5_SPREAD_FILTER_ENABLED at their defaults (both False), this
function is behavior-identical to the pre-Phase-2 implementation.
"""
from __future__ import annotations

from decimal import Decimal

from backend.core.technicals import ema as _ema_series
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _regime_atr_mult_scale,
    _regime_strategy_disabled,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "ema_trend"


def evaluate_ema_trend(ctx: StrategyContext) -> StrategySignal:
    """EMA20/50/100 stack alignment + positive/negative slope on M15 (the intelligence/trading
    EMATrendStrategy pattern, EMA200 relaxed to EMA100 -- the standard 100-bar M15 fetch used
    elsewhere in this pipeline doesn't reliably support a 200-period EMA)."""
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
    max_atr_mult = 1.5 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("3.0") if direction == "LONG" else entry - atr * Decimal("3.0")
    strength = min(100.0, 60.0 + abs(slope20) / max(float(atr), 1e-9) * 20.0)
    # EQH/EQL + squeeze: evidence-only for this strategy (not yet OOS-validated here specifically
    # -- see _eqh_eql_touch_count/_squeeze_evidence docstrings). Never adjusts strength.
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "ema100": float(ema100.iloc[-1]), "slope20": slope20,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
        "market_regime": ctx.market_regime,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=strength,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, max_atr_mult))

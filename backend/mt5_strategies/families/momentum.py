"""momentum -- worst-designed strategy in the audit by construction: two lagging oscillators
(RSI, MACD) used as direct, unconfirmed triggers with deliberately zero market-structure
involvement ("a pure momentum read", per the original design). 0/10 symbols positive, 0/9 years
positive, -0.160R pooled -- a perfect null record fully consistent with quant-community consensus
on why naive momentum systems fail without volatility/regime/session filtering.

2026-08-21 Phase 3 blueprint, Section 6: this is a DEPRECATION GUARD, not a fix -- the audit's
directive is retire-or-rebuild, and a rebuild is explicitly out of scope for this deliverable.
MT5_MOMENTUM_STRATEGY_ENABLED (default False) is a code-level circuit breaker INDEPENDENT of the
existing STRATEGY_FAMILIES activation-status system (backend/mt5_strategies/models.py) -- when
False, evaluate_momentum returns an ineligible signal immediately, before any RSI/MACD computation
runs, regardless of what activation_status() would otherwise say. Unlike every other Phase 1/2/3
flag, THIS ONE'S DEFAULT IS NOT A NO-OP: flipping this file live turns momentum off immediately,
by design, per the audit's explicit "protect capital now" directive -- it is not a "new lever,
off by default" flag like the rest of this codebase's flags. The evaluator stays registered in
EVALUATORS (see families/__init__.py) so historical/replay tracking is unaffected -- only live
signal generation is gated.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from backend.core.technicals import macd as _macd_frame
from backend.core.technicals import rsi as _rsi_series
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

_STRATEGY_ID = "momentum"


def evaluate_momentum(ctx: StrategyContext) -> StrategySignal:
    """RSI + MACD alignment, no SMC dependency -- a pure momentum read. See module docstring:
    disabled by default via MT5_MOMENTUM_STRATEGY_ENABLED pending a structural rebuild."""
    if not _env_flag("MT5_MOMENTUM_STRATEGY_ENABLED", False):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="STRATEGY_DEPRECATED_PENDING_REBUILD")
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")

    closes = _closes(ctx.m15_rows)
    if len(closes) < 40:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    rsi_series = _rsi_series(closes, 14)
    macd_frame = _macd_frame(closes)
    latest_rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else 50.0
    macd_line, signal_line = float(macd_frame["macd"].iloc[-1]), float(macd_frame["signal"].iloc[-1])
    long_aligned = latest_rsi > 55 and macd_line > signal_line
    short_aligned = latest_rsi < 45 and macd_line < signal_line
    if not (long_aligned or short_aligned):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="momentum_not_aligned")
    direction = "LONG" if long_aligned else "SHORT"
    price = float(closes.iloc[-1])
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=1.5)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    strength = 60.0 + min(30.0, abs(macd_line - signal_line) / max(abs(macd_line), 1e-9) * 30.0)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "rsi14": latest_rsi, "macd": macd_line, "macd_signal": signal_line,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))  # observability only -- see _squeeze_evidence's docstring
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, 1.5))

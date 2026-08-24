"""trend_pullback -- recovered from bad early years (2018, 2020, 2021: -0.129R, -0.233R, -0.178R)
into a real performer post-2022 (+1.313R in 2025, +0.677R in 2026; +0.111R pooled over the full
8-year audit, 7 of 10 symbols positive). The March-2021 single-month screening pass caught it
mid-recovery and called it "broken" -- the 8-year evidence reversed that conclusion. The one real
weakness that survives at every window size: entry timing on the LONG side specifically (57.4%
immediate-failure rate in the March pass).

2026-08-20 architecture blueprint (Phase 1), Section 3.4: three flagged levers plus an independent
LONG/SHORT code split. 2026-08-21 (Phase 2) adds regime-widened ATR buffers under HIGH_VOLATILITY_
EXPANSION, stale-exit metadata, and the spread safety buffer -- this strategy is NOT regime- or
session-timing-restricted per the Phase 2 spec (only breakout/ema_trend/momentum are disabled by
CHOP_RANGING; only breakout/session_breakout/momentum are session-timing-restricted). With every
flag at its default (MT5_TREND_PULLBACK_CONFLUENCE_MODE=EMA_ZONE, every boolean flag False), this
function is behavior-identical to the pre-restructure implementation. Nothing here changes
risk-per-trade or stop/target geometry formulas.
"""
from __future__ import annotations

import os
from decimal import Decimal

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_flag,
    _env_int,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _location_quality_trend_pullback,
    _no_signal,
    _ote_zone_for_direction,
    _reaction_candle_confirms,
    _recent_structure_break_against,
    _regime_atr_mult_scale,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _stale_exit_metadata,
    _wick_rejection_score,
)
from backend.mt5_strategies.models import StrategySignal
from backend.core.technicals import ema as _ema_series

_STRATEGY_ID = "trend_pullback"
_EMA_ZONE = "EMA_ZONE"
_OTE_OB_FVG = "OTE_OB_FVG"


def _confluence_mode() -> str:
    return os.getenv("MT5_TREND_PULLBACK_CONFLUENCE_MODE", _EMA_ZONE).strip().upper()


def _in_ema_zone(ema20_val: float, ema50_val: float, atr: float, price: float) -> bool:
    """Original zone definition, unchanged: price within one ATR of the EMA20/EMA50 band."""
    tolerance = atr * 1.0
    zone_low, zone_high = min(ema20_val, ema50_val) - tolerance, max(ema20_val, ema50_val) + tolerance
    return zone_low <= price <= zone_high


def _in_ote_ob_fvg_confluence(ctx: StrategyContext, direction: str, price: float) -> bool:
    """MT5_TREND_PULLBACK_CONFLUENCE_MODE=OTE_OB_FVG: reuses smc_continuation.py's exact
    retracement-zone pattern (imbalances + order_blocks, direction-filtered, non-mitigated) --
    genuine reuse, not a second implementation -- plus the OTE zone as a third confluence input.
    A pullback needs to land in at least one of {OB, FVG, OTE}, not merely "near an EMA"."""
    with_trend = "bullish" if direction == "LONG" else "bearish"
    zones = [z for z in ctx.m15_snapshot.imbalances if z.direction == with_trend and z.status != "mitigated"]
    zones += [b for b in ctx.m15_snapshot.order_blocks if b.direction == with_trend and b.status not in {"mitigated", "invalidated"}]
    if any(z.price_low is not None and z.price_high is not None and float(z.price_low) <= price <= float(z.price_high) for z in zones):
        return True
    ote = _ote_zone_for_direction(ctx, direction)
    if ote is not None:
        ote_low, ote_high = ote
        return ote_low <= price <= ote_high
    return False


def _build_signal(ctx: StrategyContext, *, direction: str, price: float, ema20_val: float, ema50_val: float, atr: float, evidence_extra: dict) -> StrategySignal:
    entry = Decimal(str(price))
    atr_d = Decimal(str(atr))
    max_atr_mult = 1.2 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr_d, min_atr_mult=1.2, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = entry + atr_d * Decimal("2.5") if direction == "LONG" else entry - atr_d * Decimal("2.5")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "ema20": ema20_val, "ema50": ema50_val, "htf_trend_h1": ctx.htf_trend_h1,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr),
        "market_regime": ctx.market_regime,
        **evidence_extra,
    }
    evidence.update(_squeeze_evidence(ctx))
    # Evidence-upgrade Steps 1-4 (docs/mean-reversion-trend-pullback-implementation-spec.md):
    # observability-only, does NOT feed `strength` above -- held to the same bar as
    # _eqh_eql_touch_count/displacement_magnitude_atr were before their own OOS validation.
    evidence["wick_rejection_score"] = _wick_rejection_score(ctx, direction)
    evidence.update(_location_quality_trend_pullback(ctx, direction=direction, price=price, atr=atr))
    metadata = _geometry_metadata(ctx, entry, stop, None, atr_d, 1.2, max_atr_mult)
    metadata.update(_stale_exit_metadata(ctx, strategy_id=_STRATEGY_ID))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=70.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=metadata)


def _evaluate_trend_pullback_long(ctx: StrategyContext, *, price: float, ema20_val: float, ema50_val: float, atr: float) -> StrategySignal:
    """Independent LONG path (Section 3.4's decoupling requirement) -- the audit's own evidence
    (14.6% win rate, -0.549R in the March pass) is concentrated here, so this side's flags can
    diverge from SHORT's without any risk of cross-contaminating a side that already works."""
    mode = _confluence_mode()
    in_zone = _in_ote_ob_fvg_confluence(ctx, "LONG", price) if mode == _OTE_OB_FVG else _in_ema_zone(ema20_val, ema50_val, atr, price)
    if not in_zone:
        reason = "not_in_confluence_zone" if mode == _OTE_OB_FVG else "not_in_pullback_zone"
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=reason)
    if ctx.htf_trend_h1 == "bearish":
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="htf_conflict")
    if _env_flag("MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED", False):
        lookback = _env_int("MT5_TREND_PULLBACK_ANTI_CHOCH_LOOKBACK_BARS", 10)
        if _recent_structure_break_against(ctx, "LONG", lookback_bars=lookback):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="RECENT_STRUCTURE_BREAK_AGAINST")
    if _env_flag("MT5_TREND_PULLBACK_REACTION_CANDLE_REQUIRED", False):
        if not _reaction_candle_confirms(ctx, "LONG"):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_REACTION_CANDLE")
    return _build_signal(ctx, direction="LONG", price=price, ema20_val=ema20_val, ema50_val=ema50_val, atr=atr, evidence_extra={"confluence_mode": mode})


def _evaluate_trend_pullback_short(ctx: StrategyContext, *, price: float, ema20_val: float, ema50_val: float, atr: float) -> StrategySignal:
    """Independent SHORT path -- mirrors LONG exactly but is a fully separate function so a
    future LONG-specific fix (e.g. from the OOS results this ships behind) can never silently
    change SHORT's behavior too, and vice versa."""
    mode = _confluence_mode()
    in_zone = _in_ote_ob_fvg_confluence(ctx, "SHORT", price) if mode == _OTE_OB_FVG else _in_ema_zone(ema20_val, ema50_val, atr, price)
    if not in_zone:
        reason = "not_in_confluence_zone" if mode == _OTE_OB_FVG else "not_in_pullback_zone"
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=reason)
    if ctx.htf_trend_h1 == "bullish":
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="htf_conflict")
    if _env_flag("MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED", False):
        lookback = _env_int("MT5_TREND_PULLBACK_ANTI_CHOCH_LOOKBACK_BARS", 10)
        if _recent_structure_break_against(ctx, "SHORT", lookback_bars=lookback):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="RECENT_STRUCTURE_BREAK_AGAINST")
    if _env_flag("MT5_TREND_PULLBACK_REACTION_CANDLE_REQUIRED", False):
        if not _reaction_candle_confirms(ctx, "SHORT"):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_REACTION_CANDLE")
    return _build_signal(ctx, direction="SHORT", price=price, ema20_val=ema20_val, ema50_val=ema50_val, atr=atr, evidence_extra={"confluence_mode": mode})


def evaluate_trend_pullback(ctx: StrategyContext) -> StrategySignal:
    """M15 EMA20/50 local trend with price pulled back into a confluence zone (EMA band by
    default; OB/FVG/OTE behind a flag), gated by H1 HTF trend non-conflict. Dispatches to the
    LONG or SHORT path based on which direction the local EMA20/50 relationship currently
    favors -- exactly one side is ever locally eligible per cycle, matching the pre-restructure
    behavior; the split only decouples their independent gates/flags, not this dispatch."""
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    closes = _closes(ctx.m15_rows)
    if len(closes) < 60:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    ema20, ema50 = _ema_series(closes, 20), _ema_series(closes, 50)
    price = float(closes.iloc[-1])
    ema20_val, ema50_val = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    atr = float(ctx.atr_m15) if ctx.atr_m15 else abs(price - ema50_val) or 0.0001
    if ema20_val > ema50_val:
        return _evaluate_trend_pullback_long(ctx, price=price, ema20_val=ema20_val, ema50_val=ema50_val, atr=atr)
    return _evaluate_trend_pullback_short(ctx, price=price, ema20_val=ema20_val, ema50_val=ema50_val, atr=atr)

"""donchian_trend_follow -- new strategy (2026-08-24), see docs/donchian-trend-follow-design-
spec.md for the full design and docs/new-strategies-redundancy-audit.md for why this is not a
duplicate of breakout/trend_pullback/smc_continuation.

Hypothesis: established directional momentum -> N-bar price breakout -> volatility confirmation
-> ride persistent trend. Deliberately stricter than breakout.py's default behavior: mandatory
volatility-expansion confirmation (breakout.py has none) and mandatory HTF alignment
(breakout.py's own HTF gate is off by default) on top of a coarser, harder-to-satisfy trigger (a
fixed N-bar Donchian channel break, not an SMC swing-structure BOS). SMC/BOS evidence is
supporting only, never the mandatory core, per the explicit design constraint.

No hard ctx.regime gate (STRATEGY_FAMILIES entry below has an empty regimes tuple) -- the
mandatory volatility-expansion condition already does the regime-relevant gating this strategy
needs; a second, separate ctx.regime check would repeat the exact double-gating problem
diagnosed and fixed for trend_pullback (see docs/mean-reversion-trend-pullback-evidence-
architecture-design.md section 4.1).

SHADOW_MT5 by default (backend/mt5_strategies/models.py) -- observation/fingerprint-accumulation
only until it clears its own 3-year point-in-time-safe validation.
"""
from __future__ import annotations

import os
from decimal import Decimal

from backend.mt5_strategies.context import REGIME_HIGH_VOLATILITY_EXPANSION, StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_float,
    _env_int,
    _eqh_eql_touch_count,
    _find_level_retest_hold,
    _geometry_metadata,
    _no_signal,
    _opposing_structural_level,
    _recent_structure_break_against,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _structural_take_profit,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "donchian_trend_follow"
_BREAK_AND_GO = "BREAK_AND_GO"
_RETEST_AND_HOLD = "RETEST_AND_HOLD"


def _donchian_breakout_at(rows: list[dict], idx: int, n: int) -> tuple[str, float] | None:
    """A fresh Donchian(n) breakout AT bar `idx`: is rows[idx]'s close beyond the channel built
    from the n bars STRICTLY BEFORE idx (point-in-time safe -- the channel never includes the bar
    being tested against it). Returns (direction, channel_edge) or None."""
    if idx - n < 0:
        return None
    window = rows[idx - n:idx]
    if len(window) < n:
        return None
    highs = [float(r["high"]) for r in window]
    lows = [float(r["low"]) for r in window]
    channel_high, channel_low = max(highs), min(lows)
    price = float(rows[idx]["close"])
    if price > channel_high:
        return "LONG", channel_high
    if price < channel_low:
        return "SHORT", channel_low
    return None


def _find_recent_donchian_breakout(rows: list[dict], n: int, lookback: int) -> tuple[int, str, float] | None:
    """Scans backward up to `lookback` bars for the most recent fresh Donchian breakout (see
    _donchian_breakout_at). Returns (bar_index, direction, channel_edge) or None -- bounded
    iteration, same cost class as breakout.py's own bos_breaks filtering."""
    last_index = len(rows) - 1
    earliest = max(n, last_index - lookback)
    for idx in range(last_index, earliest - 1, -1):
        result = _donchian_breakout_at(rows, idx, n)
        if result is not None:
            direction, channel_edge = result
            return idx, direction, channel_edge
    return None


def _volatility_expansion_confirmed(ctx: StrategyContext) -> bool:
    """Mandatory core condition (not one of Phase 2's fail-open optional gates): a genuine
    Bensim strategy trigger, so unavailable data means no signal, same fail-closed convention
    every other strategy's own history-sufficiency check already uses."""
    if ctx.market_regime == REGIME_HIGH_VOLATILITY_EXPANSION:
        return True
    if ctx.atr_expansion_ratio is None:
        return False
    return ctx.atr_expansion_ratio >= _env_float("MT5_DONCHIAN_MIN_ATR_EXPANSION_RATIO", 1.1)


def _build_signal(ctx: StrategyContext, *, direction: str, entry: Decimal, opposite_edge: float, atr: Decimal, evidence: dict, strength: float) -> StrategySignal:
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, opposite_edge, atr, min_atr_mult=1.2, max_atr_mult=3.0)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    structural = _structural_take_profit(ctx, direction=direction, entry=entry, stop=stop, atr=atr)
    if structural is not None:
        target = structural["tp1"]
        tp_basis = structural["basis"]
    else:
        fallback_mult = Decimal(str(_env_float("MT5_DONCHIAN_FALLBACK_TARGET_ATR_MULT", 4.0)))
        target = entry + atr * fallback_mult if direction == "LONG" else entry - atr * fallback_mult
        tp_basis = "atr_flat_multiple_wide"
    evidence = {**evidence, "market_regime": ctx.market_regime, "atr_expansion_ratio": ctx.atr_expansion_ratio, "take_profit_basis": tp_basis}
    evidence.update(_squeeze_evidence(ctx))
    metadata = _geometry_metadata(ctx, entry, stop, opposite_edge, atr, 1.2, 3.0)
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


def evaluate_donchian_trend_follow(ctx: StrategyContext) -> StrategySignal:
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    rows = ctx.m15_rows
    closes = _closes(rows)
    n = _env_int("MT5_DONCHIAN_PERIOD", 20)
    if len(closes) < n + 30:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    atr = ctx.atr_m15
    if not atr or atr <= 0:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_atr")
    atr_f = float(atr)

    mode = os.environ.get("MT5_DONCHIAN_ENTRY_MODE", _BREAK_AND_GO).strip().upper()
    retest_lookback = _env_int("MT5_DONCHIAN_RETEST_LOOKBACK_BARS", 20)
    found = _find_recent_donchian_breakout(rows, n, retest_lookback if mode == _RETEST_AND_HOLD else 5)
    if found is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_channel_breakout")
    breakout_bar_index, direction, channel_edge = found
    opposite_edge = min(float(r["low"]) for r in rows[breakout_bar_index - n:breakout_bar_index]) if direction == "LONG" else \
        max(float(r["high"]) for r in rows[breakout_bar_index - n:breakout_bar_index])

    breakout_price = float(rows[breakout_bar_index]["close"])
    breakout_distance_atr = abs(breakout_price - channel_edge) / atr_f
    min_breakout_atr = _env_float("MT5_DONCHIAN_MIN_BREAKOUT_ATR", 0.3)
    if breakout_distance_atr < min_breakout_atr:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="breakout_distance_below_minimum")

    if not _volatility_expansion_confirmed(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_volatility_expansion")

    expected_htf = "bullish" if direction == "LONG" else "bearish"
    if ctx.htf_trend_h1 != expected_htf:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="HTF_TREND_NOT_ALIGNED")

    if mode == _RETEST_AND_HOLD:
        hold = _find_level_retest_hold(ctx, level_bar_index=breakout_bar_index, broken_level=channel_edge, direction=direction)
        if hold is None:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_confirmed_retest_and_hold")
        confirm_bar_index, closest_price = hold
        bars_since_confirm = (len(rows) - 1) - confirm_bar_index
        if bars_since_confirm > 5:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="retest_confirm_too_old")
        entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
        entry_evidence_extra = {"retest_confirm_bar_index": confirm_bar_index, "bars_since_retest_confirm": bars_since_confirm, "closest_retest_price": closest_price}
    else:
        bars_since_breakout = (len(rows) - 1) - breakout_bar_index
        if bars_since_breakout > 5:
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="breakout_too_old")
        entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
        entry_evidence_extra = {"bars_since_breakout": bars_since_breakout}

    with_trend_direction = "bullish" if direction == "LONG" else "bearish"
    bos_supporting = any(b.break_kind == "bos" and b.direction == with_trend_direction and b.bar_index >= breakout_bar_index - 3 for b in ctx.m15_snapshot.breaks)
    no_opposing_break = not _recent_structure_break_against(ctx, direction, lookback_bars=10)
    displacement_supporting = any(d.direction == with_trend_direction and abs(d.bar_index - breakout_bar_index) <= 2 for d in ctx.m15_snapshot.displacements)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=float(entry), atr=atr_f)

    strength = 60.0
    strength += 10.0 if bos_supporting else 0.0
    strength += 8.0 if no_opposing_break else -8.0
    strength += 8.0 if displacement_supporting else 0.0
    strength += 6.0 if eqh_eql_touches >= 2 else 0.0
    opposing_level = _opposing_structural_level(ctx, direction)
    if opposing_level is not None:
        opposing_distance_atr = abs(float(opposing_level) - float(entry)) / atr_f
        if opposing_distance_atr < 1.0:
            strength -= 10.0

    evidence = {
        "donchian_period": n, "channel_edge": channel_edge, "breakout_distance_atr": round(breakout_distance_atr, 3),
        "entry_mode": mode, "htf_trend_h1": ctx.htf_trend_h1,
        "bos_supporting": bos_supporting, "no_opposing_structure_break": no_opposing_break,
        "displacement_supporting": displacement_supporting, "eqh_eql_touch_count": eqh_eql_touches,
        "opposing_structural_level": float(opposing_level) if opposing_level is not None else None,
        **entry_evidence_extra,
    }
    return _build_signal(ctx, direction=direction, entry=entry, opposite_edge=opposite_edge, atr=atr, evidence=evidence, strength=max(50.0, min(100.0, strength)))

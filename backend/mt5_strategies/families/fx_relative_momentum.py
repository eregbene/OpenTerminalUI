"""fx_relative_momentum -- new strategy (2026-08-24, reprioritized ahead of strategies #2/#4 per
explicit instruction: Bensim needs a strategy capable of reasonable DEMO trade frequency, and
cross-sectional currency momentum has real published research behind it -- see
backend/mt5_strategies/currency_strength.py's module docstring for the methodology citation).

Hypothesis: buy the strong currency / sell the weak currency. The cross-sectional strength
ranking (computed once per cycle across all symbols, see currency_strength.py) IS the mandatory
core -- deliberately NOT gated behind RSI extreme, SMC structure, FVG/OB, EQH/EQL, liquidity
sweep, or ctx.regime alignment simultaneously. HTF trend, market structure, and spread safety are
supporting/safety checks only, recorded separately, never stacked into a mandatory AND-chain.

Distinct from the existing `momentum` strategy (families/momentum.py): that is TIME-SERIES
momentum -- one pair's own RSI/MACD alignment against its own price history, hard-disabled by a
code-level circuit breaker after a 0/10-symbols-positive audit record. This is CROSS-SECTIONAL
momentum -- a currency's return relative to a basket of OTHER currencies, ranked, traded as the
spread between the strongest and weakest. Different math, different data (7-currency basket vs
single pair), different failure mode -- not a resurrection of the deprecated strategy.

XAUUSD is not a currency pair and is never a candidate here (see currency_strength.py). CAD
contributes to USD's strength via USDCAD but is never itself ranked or traded (matches the
brief's 7-currency list exactly: USD/EUR/GBP/JPY/CHF/AUD/NZD).

SHADOW/DISABLED by default (backend/mt5_strategies/models.py) until its own validation clears.
"""
from __future__ import annotations

from decimal import Decimal

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.currency_strength import _PAIR_CURRENCIES
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_float,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _recent_structure_break_against,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _structural_take_profit,
    _zone_overlap,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "fx_relative_momentum"


def evaluate_fx_relative_momentum(ctx: StrategyContext) -> StrategySignal:
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    if ctx.symbol not in _PAIR_CURRENCIES:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NOT_A_CURRENCY_PAIR")
    cs = ctx.currency_strength
    if not cs or not cs.get("strength") or not cs.get("rank"):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_CURRENCY_STRENGTH_DATA")

    base, quote = _PAIR_CURRENCIES[ctx.symbol]
    base_strength, quote_strength = cs["strength"].get(base), cs["strength"].get(quote)
    if base_strength is None or quote_strength is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="CURRENCY_NOT_RANKED")
    base_rank, quote_rank = cs["rank"].get(base), cs["rank"].get(quote)

    strength_spread = base_strength - quote_strength
    min_spread = _env_float("MT5_FX_RELATIVE_MOMENTUM_MIN_SPREAD_ATR", 0.5)
    if abs(strength_spread) < min_spread:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="strength_spread_below_minimum")
    direction = "LONG" if strength_spread > 0 else "SHORT"

    closes = _closes(ctx.m15_rows)
    if len(closes) < 20:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")
    price = float(closes.iloc[-1])
    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    atr = ctx.atr_m15
    if not atr or atr <= 0:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_atr")

    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=1.5)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    structural = _structural_take_profit(ctx, direction=direction, entry=entry, stop=stop, atr=atr)
    if structural is not None:
        target, tp_basis = structural["tp1"], structural["basis"]
    else:
        target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
        tp_basis = "atr_flat_multiple"

    # Supporting/safety evidence -- entry quality only, NEVER mandatory (explicit design
    # constraint: the cross-sectional ranking alone is the core signal).
    htf_alignment = ctx.htf_trend_h1 == ("bullish" if direction == "LONG" else "bearish")
    with_trend_direction = "bullish" if direction == "LONG" else "bearish"
    bos_supporting = any(b.break_kind == "bos" and b.direction == with_trend_direction for b in ctx.m15_snapshot.breaks[-10:])
    no_opposing_break = not _recent_structure_break_against(ctx, direction, lookback_bars=10)
    zone_overlap = _zone_overlap(ctx, zone_direction=with_trend_direction, price=price)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr))

    base_persistence = cs.get("pair_momentum", {}).get(ctx.symbol, {}).get("persistence")
    momentum_persistence = bool(base_persistence)

    strength = 60.0
    strength += min(15.0, abs(strength_spread) * 5.0)  # bounded, scales with spread magnitude
    strength += 8.0 if htf_alignment else -4.0
    strength += 5.0 if momentum_persistence else 0.0
    strength += 4.0 if bos_supporting else 0.0
    strength += 4.0 if no_opposing_break else -6.0
    strength += 3.0 if zone_overlap else 0.0

    evidence = {
        "base_currency": base, "quote_currency": quote,
        "base_currency_strength": round(base_strength, 4), "quote_currency_strength": round(quote_strength, 4),
        "strength_spread": round(strength_spread, 4), "base_rank": base_rank, "quote_rank": quote_rank,
        "momentum_lookback": cs.get("horizon"), "momentum_persistence": momentum_persistence,
        "htf_alignment": htf_alignment, "htf_trend_h1": ctx.htf_trend_h1,
        "smc_context": {
            "bos_supporting": bos_supporting, "no_opposing_structure_break": no_opposing_break,
            "with_trend_zone_overlap": zone_overlap, "eqh_eql_touch_count": eqh_eql_touches,
        },
        "market_regime": ctx.market_regime, "take_profit_basis": tp_basis,
    }
    evidence.update(_squeeze_evidence(ctx))
    metadata = _geometry_metadata(ctx, entry, stop, None, atr, 1.5, 1.5)
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=max(50.0, min(100.0, strength)),
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)

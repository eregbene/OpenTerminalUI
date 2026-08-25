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
    _env_flag,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _location_quality_mean_reversion,
    _nearest_liquidity_level_atr_distance,
    _no_signal,
    _premium_discount_position,
    _regime_atr_mult_scale,
    _regime_strategy_disabled,
    _regime_strength_bonus,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _wick_rejection_score,
    _zone_overlap,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "mean_reversion"

# 2026-08-25 deep confidence audit -- Part 4 (mean_reversion quality score). Unlike
# trend_pullback, this strategy's `strength` (55 + RSI-extremity bonus + regime bonus) IS already
# genuinely graduated -- but confidence.py's trend_multi_timeframe fallback reuses it labeled as
# "trend", which is conceptually backwards here: the real audit found RSI extremity is
# NON-monotonic (Q1 20-25 extremity: exp=+0.570 best; Q4 33-44 most-extreme: exp=-0.525 worst),
# and directional HTF trend agreement is consistently NEGATIVE evidence for a counter-trend
# strategy (bullish/bullish: exp=-1.00R n=7; transitional/transitional: exp=+0.387R n=57) -- the
# opposite polarity from trend_pullback's own (correct, WITH-trend) use of the same ctx fields.
# This builds a real mean_reversion-specific quality score with that opposite polarity baked in,
# fed through the same strategy-agnostic context["trend_quality_score"] hook.
#
# Redundancy check performed before writing this (per explicit instruction, not assumed):
#   - MT5_REGIME_FILTER_ENABLED is FALSE on the live container today (verified directly), which
#     means _regime_strength_bonus/_regime_strategy_disabled's CHOP_RANGING/TRENDING_STRONG
#     handling is CURRENTLY INERT in production -- htf_neutrality_score below reads ctx.adx_m15/
#     htf_trend_h1/h4 directly (already computed by build_strategy_context, zero recomputation)
#     and does not depend on that flag, so there is no double-counting today. If
#     MT5_REGIME_FILTER_ENABLED is ever turned on later, both mechanisms would be scoring
#     regime-suitability from different angles (a hard CHOP_RANGING bonus to `strength` vs. a
#     graduated HTF-directionality read here) -- worth a second look at that time, not now.
#   - location_score/liquidity_interaction_score below reuse the exact same underlying primitives
#     as _location_quality_mean_reversion (_premium_discount_position, _nearest_liquidity_level_
#     atr_distance, _eqh_eql_touch_count, _zone_overlap) -- deliberately NOT that composite
#     itself, since the real audit found location_quality_score non-monotonic and unreliable
#     (spec Part 5: "test primitives independently, don't blindly reuse the composite"). Each
#     primitive is scored on its own graduated curve here instead of summed into one sub-linear
#     factor-count.
#   - VWAP (user's item 2) is NOT built here: the only VWAP implementation in this codebase is
#     inline inside vwap_reversion.py (a SHADOW strategy this pass explicitly does not touch),
#     not a shared/reusable primitive -- extracting it would be a shared-framework change out of
#     this pass's scope. _premium_discount_position (dealing-range equilibrium, already real and
#     already computed) serves as the equilibrium/stretch proxy instead -- genuinely close in
#     concept (distance from a range-derived fair-value anchor), not a stand-in guess.
_MR_RSI_EXTREMITY_MIN = 20.0  # validity floor: |50-rsi| is always >= 20 when this strategy fires
_MR_RSI_EXTREMITY_PEAK = 30.0  # hypothesis under test (Part 7 validates/revises this, not assumed true)
_MR_RSI_EXTREMITY_MAX = 50.0  # RSI in [0,100] caps |50-rsi| at 50
_MR_ADX_NEUTRAL_FLOOR = 15.0
_MR_ADX_NEUTRAL_CEILING = 40.0
_MR_ADX_PENALTY_MAX = 30.0
_MR_LIQUIDITY_PROXIMITY_ATR_CEILING = 1.5
_MR_EQH_EQL_TOUCH_CEILING = 3.0
_MR_QUALITY_WEIGHTS = {"rsi_extremity": 0.25, "htf_neutrality": 0.30, "location": 0.25, "liquidity_interaction": 0.20}


def _mr_clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _mr_rsi_extremity_score(latest_rsi: float) -> float:
    """NON-monotonic by design (Part 1 item): peaks at the hypothesized sweet spot, tapers at
    BOTH the validity floor (barely extreme) and maximal extremity -- the real audit found the
    most extreme readings performed worst, so this deliberately does not reward "more extreme is
    always better", unlike a naive linear extension of the strategy's own `strength` formula."""
    extremity = abs(50.0 - latest_rsi)
    if extremity <= _MR_RSI_EXTREMITY_PEAK:
        return _mr_clamp01((extremity - _MR_RSI_EXTREMITY_MIN) / (_MR_RSI_EXTREMITY_PEAK - _MR_RSI_EXTREMITY_MIN)) * 100.0
    return _mr_clamp01(1.0 - (extremity - _MR_RSI_EXTREMITY_PEAK) / (_MR_RSI_EXTREMITY_MAX - _MR_RSI_EXTREMITY_PEAK)) * 100.0


def _mean_reversion_quality_score(
    ctx: StrategyContext, *, direction: str, price: float, latest_rsi: float, atr: float,
) -> tuple[float, dict] | tuple[None, None]:
    """Feature-flagged (default OFF until validated): returns (score, breakdown) or (None, None)
    when disabled or ATR is unusable -- never fabricates a score; a failure here degrades to
    confidence.py's existing ranking_score fallback, same contract as trend_pullback's version."""
    if not _env_flag("MT5_MEAN_REVERSION_QUALITY_SCORE_ENABLED", False) or atr <= 0:
        return None, None
    try:
        rsi_extremity_score = _mr_rsi_extremity_score(latest_rsi)

        # ctx.htf_trend_h1/h4 and ctx.adx_m15 are ALREADY computed once per cycle by
        # build_strategy_context -- reused verbatim. Opposite polarity from trend_pullback's own
        # use of the same fields: here, a firm directional HTF read and high ADX are NEGATIVE
        # evidence (strong trend fights a reversion trade), not positive.
        #
        # 2026-08-25 validation finding: retroactively rescoring the real 174-candidate resolved
        # population with the FIRST version of this function (symmetric directional_count-based
        # base) produced a non-monotonic quality[40-60) band (n=110, exp=-0.235R) sandwiched
        # between a positive band below it. Diagnosed directly: within that band,
        # htf_trend_h1="bullish" rows (34/110) carried exp=-1.000R/wr=0.00, while
        # htf_trend_h1="transitional" rows (63/110, the majority) carried exp=+0.178R -- and the
        # original full-population audit found htf=(bullish,bullish) (a firm AGREEING trend on
        # both timeframes) at exp=-1.00R/n=7 vs htf=(transitional,transitional) at exp=+0.387R/
        # n=57, the best-supported combination in the data. Directional_count treated "H1
        # directional, H4 not" the same as "H1 directional, H4 agrees too" (both counted as 1 or
        # 2 generically) -- it under-penalizes exactly the FIRM, AGREEING trend case the data
        # says is worst, and doesn't reward the both-transitional case enough. Replaced with a
        # direct read of trend AGREEMENT (not just directional count), matching the real
        # evidence -- a hypothesis from a 2-week/179-candidate sample, due for re-validation as
        # more DEMO data accumulates, not an assumed permanent truth.
        # 2026-08-25 SECOND revision, this time against the REAL 8-year/52,633-row historical
        # corpus already persisted from an earlier session's backfill (HistoricalPatternFinger-
        # printORM.h1_trend/h4_trend ARE ctx.htf_trend_h1/h4). That large-N check told a
        # DIFFERENT story than the 2-week/174-candidate DEMO sample this function was first built
        # from: real ordering (best to worst) is one_directional (n=23807, exp=+0.347) >
        # conflicting_directional (n=9805, exp=+0.265) > both_transitional (n=8390, exp=+0.214)
        # > firm_agreeing_trend (n=10631, exp=+0.155) -- EVERY bucket is net positive at scale,
        # and "both transitional" is NOT the best case as the small sample suggested (it's third
        # of four). Only the qualitative claim that a firm, agreeing directional trend is the
        # weakest condition survived -- and even that gap is modest (0.155 vs 0.347), not the
        # dramatic swing the small sample implied. Scores below match the REAL ordering and a
        # proportionally modest spread, not the original hypothesis.
        h1, h4 = ctx.htf_trend_h1, ctx.htf_trend_h4
        h1_directional = h1 in ("bullish", "bearish")
        h4_directional = h4 in ("bullish", "bearish")
        if h1_directional and h4_directional and h1 == h4:
            htf_base = 45.0  # firm, agreeing directional trend -- real weakest tier, still positive
        elif h1_directional and h4_directional:
            htf_base = 85.0  # both directional but conflicting -- real 2nd-best tier
        elif h1_directional or h4_directional:
            htf_base = 100.0  # exactly one timeframe directional -- real best tier
        else:
            htf_base = 70.0  # both transitional/ranging/unknown -- real 3rd-best tier
        adx = ctx.adx_m15
        if adx is not None:
            adx_penalty = _mr_clamp01((float(adx) - _MR_ADX_NEUTRAL_FLOOR) / (_MR_ADX_NEUTRAL_CEILING - _MR_ADX_NEUTRAL_FLOOR)) * _MR_ADX_PENALTY_MAX
            htf_neutrality_score = max(0.0, htf_base - adx_penalty)
        else:
            htf_neutrality_score = htf_base

        # location_score: same underlying primitives as _location_quality_mean_reversion, scored
        # independently and gradedly rather than summed into that (empirically non-monotonic)
        # composite.
        pd_position = _premium_discount_position(ctx)
        pd_score = None
        if pd_position is not None:
            favorable = (1.0 - pd_position) if direction == "LONG" else pd_position
            pd_score = _mr_clamp01(favorable) * 100.0
        level_dist = _nearest_liquidity_level_atr_distance(ctx, price=price, atr=atr)
        level_score = None
        if level_dist is not None:
            level_score = _mr_clamp01(1.0 - level_dist / _MR_LIQUIDITY_PROXIMITY_ATR_CEILING) * 100.0
        location_parts = [s for s in (pd_score, level_score) if s is not None]
        location_score = (sum(location_parts) / len(location_parts)) if location_parts else None

        eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
        touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr)
        touch_score = _mr_clamp01(touches / _MR_EQH_EQL_TOUCH_CEILING) * 100.0
        opposing_zone_direction = "bearish" if direction == "LONG" else "bullish"
        zone_hit = _zone_overlap(ctx, zone_direction=opposing_zone_direction, price=price)
        zone_score = 100.0 if zone_hit else 30.0
        liquidity_interaction_score = 0.5 * touch_score + 0.5 * zone_score
    except Exception:
        return None, None

    weights = dict(_MR_QUALITY_WEIGHTS)
    components = {"rsi_extremity": rsi_extremity_score, "htf_neutrality": htf_neutrality_score, "location": location_score, "liquidity_interaction": liquidity_interaction_score}
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None, None
    if len(available) < len(components):
        total_weight = sum(weights[k] for k in available)
        weights = {k: weights[k] / total_weight for k in available}
    score = sum(available[k] * weights[k] for k in available)

    breakdown = {
        "latest_rsi": latest_rsi, "rsi_extremity_score": round(rsi_extremity_score, 2),
        "htf_trend_h1": ctx.htf_trend_h1, "htf_trend_h4": ctx.htf_trend_h4, "htf_directional_count": sum([h1_directional, h4_directional]),
        "adx_m15": adx, "htf_neutrality_score": round(htf_neutrality_score, 2),
        "premium_discount_position": pd_position, "pd_score": round(pd_score, 2) if pd_score is not None else None,
        "nearest_liquidity_level_atr_distance": level_dist, "level_score": round(level_score, 2) if level_score is not None else None,
        "location_score": round(location_score, 2) if location_score is not None else None,
        "eqh_eql_touch_count": touches, "touch_score": round(touch_score, 2),
        "opposing_ob_fvg_overlap": zone_hit, "zone_score": zone_score, "liquidity_interaction_score": round(liquidity_interaction_score, 2),
    }
    return round(_mr_clamp01(score / 100.0) * 100.0, 2), breakdown


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
    trend_quality_score, trend_quality_breakdown = _mean_reversion_quality_score(ctx, direction=direction, price=price, latest_rsi=latest_rsi, atr=float(atr))
    if trend_quality_score is not None:
        evidence["trend_quality_score"] = trend_quality_score
        evidence["trend_quality_breakdown"] = trend_quality_breakdown
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=max(50.0, min(100.0, strength)),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.2, max_atr_mult))

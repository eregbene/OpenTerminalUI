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

# 2026-08-25 deep confidence audit -- Part 2 (trend_pullback quality score). `strength` above is
# a near-constant 70/74/78 (only the fusion confirmation bonus moves it; real distribution:
# {70.0: 84, 74.0: 55, 78.0: 6} across 145 real DEMO candidates), so trend_multi_timeframe's
# generic ranking_score fallback in confidence.py carries almost no strategy-specific
# information about pullback QUALITY -- only "did the strategy trigger". This is the fix: a
# genuine, graduated 0-100 quality score fed through confidence.py's existing (already
# strategy-agnostic) context["trend_quality_score"] hook, the same hook MTFAI1 V2's own
# _mtfai1_v2_trend_quality (backend/brokers/mt5/autonomous.py) established. confidence.py itself
# is NOT touched -- this lives entirely in the strategy that owns the semantics, matching the
# explicit "do not turn confidence.py into strategy-specific spaghetti" instruction.
#
# Redundancy check performed before writing this (per explicit instruction, not assumed):
#   - structure_confluence (confidence.py, 18% weight) reads entry_quality.total_score, a
#     GENERIC, undirected SMC/ICT structure score (OB/FVG/sweep/BOS/CHoCH presence anywhere in
#     the M15 window) computed identically for every strategy. It does not know this candidate's
#     direction or which EMA zone it pulled back into -- no overlap with anything below.
#   - The existing htf_conflict gate below only blocks the exact OPPOSITE HTF direction (binary,
#     H1-only) -- it does not distinguish "H1 agrees" from "H1 is transitional", which the real
#     audit found catastrophic (htf_trend_h1=transitional: n=45, exp=-0.931R) yet currently
#     invisible to confidence. htf_structure_score below is a genuinely new, graduated read of
#     the SAME already-computed ctx.htf_trend_h1/h4 fields (no recomputation), not a duplicate
#     gate.
#   - The optional MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED flag (default OFF) already wires
#     _recent_structure_break_against as a hard PASS/FAIL gate. Rather than build a second,
#     duplicate opposing-CHoCH detector for the quality score, structure_intact_score below
#     reuses that exact same existing helper as a bounded NEGATIVE score modifier -- one
#     detector, two consumption modes (hard gate when the flag is on, graduated signal here
#     always), never two implementations of "was there a recent break against this trade".
#   - EMA20/EMA50 relative order (LONG/SHORT eligibility) and confluence-zone membership (the
#     `in_zone` check) are both binary validity gates already passed by the time this score is
#     computed -- ema_structure_score/pullback_quality_score below score information WITHIN the
#     already-valid state (how strong is the separation, how extended is price inside the zone),
#     never re-testing the gate itself.
_TREND_QUALITY_ADX_FLOOR = 15.0
_TREND_QUALITY_ADX_CEILING = 40.0
_TREND_QUALITY_MA_SEP_ATR_CEILING = 1.0
_TREND_QUALITY_EMA_SLOPE_LOOKBACK_BARS = 5
_TREND_QUALITY_EMA_SLOPE_ATR_CEILING = 0.15
_TREND_QUALITY_STRUCTURE_BREAK_AGAINST_PENALTY = 60.0
_TREND_QUALITY_WEIGHTS = {"trend_strength": 0.25, "htf_structure": 0.30, "ema_structure": 0.20, "pullback_quality": 0.25}
# See regime_quality_score's own comment below for provenance/caveats -- real per-regime
# expectancy from the 2026-08-25 retroactive validation (145 candidates): trending_down was the
# best regime (+0.441R), breakout and trending_up the worst (-0.925R/-1.002R, small-n on the
# latter). Neutral (60) default for any regime not in this table.
_TREND_QUALITY_REGIME_SCORE = {"trending_down": 90.0, "trending_up": 25.0, "breakout": 15.0}


def _tq_clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _trend_pullback_quality_score(
    ctx: StrategyContext, *, direction: str, price: float, ema20_val: float, ema50_val: float, ema20_slope: float | None, atr: float,
) -> tuple[float, dict] | tuple[None, None]:
    """Feature-flagged (default OFF until validated): returns (score, breakdown) or (None, None)
    when disabled, ATR is unusable, or any input is unavailable -- NEVER fabricates a score, and
    a failure here degrades to confidence.py's existing ranking_score fallback, exactly like
    MTFAI1 V2's own trend-quality hook."""
    if not _env_flag("MT5_TREND_PULLBACK_QUALITY_SCORE_ENABLED", False) or atr <= 0:
        return None, None
    try:
        adx = ctx.adx_m15
        trend_strength_score = (
            _tq_clamp01((float(adx) - _TREND_QUALITY_ADX_FLOOR) / (_TREND_QUALITY_ADX_CEILING - _TREND_QUALITY_ADX_FLOOR)) * 100.0
            if adx is not None else None
        )

        # ctx.htf_trend_h1/h4 are ALREADY the swing-based classify_trend() labels (analyze_bars,
        # computed once per cycle by build_strategy_context) -- reused verbatim, zero
        # recomputation, exactly the field the real audit found non-monotonic/ungated.
        #
        # 2026-08-25 validation finding: retroactively rescoring the real 122-candidate resolved
        # population with the FIRST version of this function (plain 0/50/100-by-agree-count)
        # produced a non-monotonic quality[60-80) band (n=77, exp=-0.566R) sandwiched between two
        # positive bands -- diagnosed directly: within that band, htf_trend_h1="transitional"
        # rows (42/77, 55% of the band) carried exp=-0.926R/wr=0.02, while htf_trend_h1="bullish"
        # rows in the SAME band were near breakeven (exp=-0.082). The agree-count scheme gave a
        # transitional-H1-but-H4-agrees row the same 50 as a bullish-H1-but-H4-disagrees row,
        # even though the first is empirically far worse -- H1 (closer to the M15 entry) needs
        # more weight than a simple two-way count gives it. Weights below are a direct response
        # to that real finding, not an assumption; re-validate as more DEMO data accumulates.
        # 2026-08-25 SECOND revision, this time against the REAL 8-year/23,966-row historical
        # corpus already persisted from an earlier session's backfill (HistoricalPatternFinger-
        # printORM.h1_trend/h4_trend ARE ctx.htf_trend_h1/h4 -- same fields, no proxy needed).
        # That large-N check confirmed the ORDERING above (both_agree > h1_agrees_only >
        # h1_transitional) but showed the MAGNITUDE from the 2-week/122-candidate DEMO sample was
        # too extreme: h1_transitional is n=12898/exp=+0.040R at full scale -- still net POSITIVE,
        # not the -0.926R catastrophe the small sample showed. The 25.0 penalty below was
        # recalibrated up to reflect that reality; both_agree=100/h1_agrees_only=70 are left
        # close to their prior values since the real data confirms those two ARE the best tiers,
        # in the right order, just needed the floor raised.
        want = "bullish" if direction == "LONG" else "bearish"
        h1_agrees = ctx.htf_trend_h1 == want
        h1_transitional = ctx.htf_trend_h1 not in ("bullish", "bearish")
        h4_agrees = ctx.htf_trend_h4 == want
        if h1_agrees and h4_agrees:
            htf_structure_score = 100.0
        elif h1_agrees:
            htf_structure_score = 70.0
        elif h1_transitional:
            htf_structure_score = 45.0
        else:  # h1 opposes outright -- already excluded by the existing htf_conflict gate in
            # practice (evaluate_trend_pullback never reaches here for this case), kept as a
            # defensive floor rather than assumed unreachable.
            htf_structure_score = 0.0
        agree_count = sum(1 for t in (ctx.htf_trend_h1, ctx.htf_trend_h4) if t == want)

        ma_sep_atr = abs(ema20_val - ema50_val) / atr
        separation_score = _tq_clamp01(ma_sep_atr / _TREND_QUALITY_MA_SEP_ATR_CEILING) * 100.0
        slope_atr = (float(ema20_slope) / atr) if ema20_slope is not None else 0.0
        slope_aligned_atr = slope_atr if direction == "LONG" else -slope_atr
        slope_score = _tq_clamp01(slope_aligned_atr / _TREND_QUALITY_EMA_SLOPE_ATR_CEILING) * 100.0 if slope_aligned_atr > 0 else 0.0
        ema_structure_score = 0.6 * separation_score + 0.4 * slope_score

        distance_from_ema20_atr = abs(price - ema20_val) / atr
        extension_score = _tq_clamp01(1.0 - distance_from_ema20_atr) * 100.0
        structure_break_against = _recent_structure_break_against(ctx, direction, lookback_bars=10)
        structure_intact_score = (100.0 - _TREND_QUALITY_STRUCTURE_BREAK_AGAINST_PENALTY) if structure_break_against else 100.0
        # 2026-08-25 validation finding: the same retroactive rescoring pass found the toxic
        # quality[60-80) band was also 39% market_regime="breakout" (n=30, exp=-0.915R). NOTE:
        # this evidence field, and the "market_regime" column persisted on candidate rows, is
        # ACTUALLY ctx.regime (adaptive_management.service.detect_regime -- slope/ATR/volatility
        # based, lowercase labels: trending_down/trending_up/breakout/ranging/low_volatility/
        # reversal/unstable_transition/event_driven), NOT ctx.market_regime (classify_market_
        # regime -- ADX/ATR-expansion based, UPPERCASE labels: TRENDING_STRONG/CHOP_RANGING/
        # HIGH_VOLATILITY_EXPANSION/QUIET_COMPRESSION/NEUTRAL) -- verified directly against
        # candidate_evaluation.py's row-population precedence (context["regime"] wins over
        # context["market_regime"]). An earlier version of this line read ctx.market_regime,
        # which never matches these keys and silently fell back to neutral(60) for every
        # candidate -- fixed to read ctx.regime, the field this evidence and the diagnosis above
        # actually refer to. Per explicit instruction this does NOT remove or gate the breakout
        # regime (the strategy stays eligible in it) -- it only makes the QUALITY SCORE reflect
        # that breakout has been this strategy's worst real regime, the same "graduated score,
        # not a hard gate" treatment MTFAI1 V2 uses for ADX. REGIME_QUALITY is a hypothesis from
        # a 2-week/145-candidate sample, not an assumed permanent truth -- neutral (60) for any
        # regime not seen in that sample, and due for re-validation as more DEMO data accumulates.
        regime_quality_score = _TREND_QUALITY_REGIME_SCORE.get(ctx.regime, 60.0)
        pullback_quality_score = 0.35 * extension_score + 0.30 * structure_intact_score + 0.35 * regime_quality_score
    except Exception:
        return None, None

    weights = dict(_TREND_QUALITY_WEIGHTS)
    if trend_strength_score is None:
        # Insufficient M15 history for ADX(14) -- redistribute its weight rather than fabricate
        # a neutral reading, same contract as MTFAI1 V2's own trend-quality function.
        del weights["trend_strength"]
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}
        score = (htf_structure_score * weights["htf_structure"] + ema_structure_score * weights["ema_structure"] + pullback_quality_score * weights["pullback_quality"])
    else:
        score = (
            trend_strength_score * weights["trend_strength"] + htf_structure_score * weights["htf_structure"]
            + ema_structure_score * weights["ema_structure"] + pullback_quality_score * weights["pullback_quality"]
        )

    breakdown = {
        "adx_m15": adx, "trend_strength_score": trend_strength_score,
        "htf_trend_h1": ctx.htf_trend_h1, "htf_trend_h4": ctx.htf_trend_h4, "htf_agree_count": agree_count, "htf_structure_score": htf_structure_score,
        "ma_separation_atr": round(ma_sep_atr, 4), "separation_score": round(separation_score, 2),
        "ema20_slope_atr": round(slope_atr, 4), "slope_score": round(slope_score, 2), "ema_structure_score": round(ema_structure_score, 2),
        "distance_from_ema20_atr": round(distance_from_ema20_atr, 4), "extension_score": round(extension_score, 2),
        "structure_break_against": structure_break_against, "structure_intact_score": structure_intact_score,
        "regime": ctx.regime, "regime_quality_score": regime_quality_score,
        "pullback_quality_score": round(pullback_quality_score, 2),
    }
    return round(_tq_clamp01(score / 100.0) * 100.0, 2), breakdown


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


def _build_signal(ctx: StrategyContext, *, direction: str, price: float, ema20_val: float, ema50_val: float, ema20_slope: float | None, atr: float, evidence_extra: dict) -> StrategySignal:
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
    trend_quality_score, trend_quality_breakdown = _trend_pullback_quality_score(
        ctx, direction=direction, price=price, ema20_val=ema20_val, ema50_val=ema50_val, ema20_slope=ema20_slope, atr=atr,
    )
    if trend_quality_score is not None:
        evidence["trend_quality_score"] = trend_quality_score
        evidence["trend_quality_breakdown"] = trend_quality_breakdown
    metadata = _geometry_metadata(ctx, entry, stop, None, atr_d, 1.2, max_atr_mult)
    metadata.update(_stale_exit_metadata(ctx, strategy_id=_STRATEGY_ID))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=70.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=metadata)


def _evaluate_trend_pullback_long(ctx: StrategyContext, *, price: float, ema20_val: float, ema50_val: float, ema20_slope: float | None, atr: float) -> StrategySignal:
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
    return _build_signal(ctx, direction="LONG", price=price, ema20_val=ema20_val, ema50_val=ema50_val, ema20_slope=ema20_slope, atr=atr, evidence_extra={"confluence_mode": mode})


def _evaluate_trend_pullback_short(ctx: StrategyContext, *, price: float, ema20_val: float, ema50_val: float, ema20_slope: float | None, atr: float) -> StrategySignal:
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
    return _build_signal(ctx, direction="SHORT", price=price, ema20_val=ema20_val, ema50_val=ema50_val, ema20_slope=ema20_slope, atr=atr, evidence_extra={"confluence_mode": mode})


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
    # Genuinely new input for the trend-quality score (Part 2, item 4): is EMA20 still moving in
    # the pullback's favor, or flattening/reversing even though it's still on the "right" side of
    # EMA50? len(closes) >= 60 is already guaranteed above, so index -1-LOOKBACK is always safe.
    ema20_slope = float(ema20.iloc[-1]) - float(ema20.iloc[-1 - _TREND_QUALITY_EMA_SLOPE_LOOKBACK_BARS])
    atr = float(ctx.atr_m15) if ctx.atr_m15 else abs(price - ema50_val) or 0.0001
    if ema20_val > ema50_val:
        return _evaluate_trend_pullback_long(ctx, price=price, ema20_val=ema20_val, ema50_val=ema50_val, ema20_slope=ema20_slope, atr=atr)
    return _evaluate_trend_pullback_short(ctx, price=price, ema20_val=ema20_val, ema50_val=ema50_val, ema20_slope=ema20_slope, atr=atr)

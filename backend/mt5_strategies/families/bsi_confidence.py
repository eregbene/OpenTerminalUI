"""bsi Part 12 -- strategy-specific confidence/quality model (DESIGN + reference
implementation, NOT WIRED anywhere). No caller in this codebase imports this module; it exists so
the Part 12 design is concrete and testable once real OOS outcome data exists, not merely
described in prose.

Explicit instruction this module follows literally: "If the mentor model works, don't simply push
it through Bensim's existing generic 75-confidence architecture... build a strategy-specific
quality model... avoid double counting... determine the confidence threshold empirically from
historical/OOS outcome bands. Do NOT automatically use 75."

Why NOT Bensim's existing generic confidence engine (backend/brokers/mt5/confidence.py): that
engine's `structure_confluence` component (18% weight) is a single, direction-agnostic "is SOME
SMC structure present nearby" signal, built for strategies whose OWN trigger logic doesn't already
consume structure/liquidity/FVG/OB directly (ema_trend, momentum, vwap_reversion, etc.). For
bsi, structure/liquidity/FVG/OB ARE the trigger -- every valid bsi signal already
required a qualifying break + a liquidity target + (usually) an unmitigated entry array before it
could exist at all. Running that same evidence through a SECOND, generic structure-presence bonus
would double-count the mentor's own gating logic as if it were independent confirmation -- exactly
the double-counting trap Part 12 explicitly warns against ("MSS + BOS + displacement + FVG caused
by the same move cannot automatically become four independent confidence bonuses").

DESIGN: six candidate dimensions, each 0-100, each measuring something the mentor's own
methodology treats as a genuinely SEPARATE quality axis (not a repackaging of the same underlying
break/leg). Dimensions are additive only after being justified individually against the mentor's
own stated preferences (see each function's docstring for its specific citation) -- this module
does NOT claim all six are independently predictive; that determination is exactly what Part 11's
component-attribution backtest (bsi.py::evaluate_bsi_component_stage) is FOR. Until
that OOS evidence exists, `COMPONENT_WEIGHTS` below are a reasoned STARTING allocation (documented
per-weight rationale, not fit to any data), and `DEFAULT_THRESHOLD` is explicitly marked
UNCALIBRATED -- this module refuses to silently claim 75 (or any other number) is correct.
"""
from __future__ import annotations

from typing import Any

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.models import StrategySignal

# ------------------------------------------------------------------------------------------
# 1. STRUCTURE QUALITY -- how clean/decisive was the qualifying break itself (row: mentor never
#    gives a numeric structure-break quality rule; this reuses Bensim's own already-computed
#    break_distance_atr, which the engine's own quality_score already partially reflects, scaled
#    independently here so it isn't re-derived from the same displacement bonus dimension 3 uses).
# ------------------------------------------------------------------------------------------
def structure_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    break_id = signal.evidence.get("structure_break_id") if signal.evidence else None
    if not break_id:
        return 50.0  # no identifiable single break (retest-only subtypes: new_york_session/under_over/abcd) -- neutral, not zero
    brk = next((b for b in ctx.m15_snapshot.breaks if b.id == break_id), None)
    if brk is None or brk.break_distance_atr is None:
        return 50.0
    return max(0.0, min(100.0, brk.break_distance_atr * 40.0))


# ------------------------------------------------------------------------------------------
# 2. LIQUIDITY QUALITY -- mentor's own repeated, explicit dual-mandatory framing ("if there's no
#    liquidity you're agreeing to lose"): rewards a target liquidity pool with real touch history
#    (EQH/EQL) over a single-touch swing level, AND rewards an inducement sweep preceding the
#    break when evidenced (row 6/23) -- these are two DISTINCT liquidity facts (target depth,
#    precondition sweep), not the same fact counted twice.
# ------------------------------------------------------------------------------------------
def liquidity_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    score = 40.0
    if signal.evidence.get("liquidity_sweep_precedes_break"):
        score += 30.0
    target = signal.take_profit
    if target is not None:
        direction = signal.direction
        want_side = "buy_side" if direction == "LONG" else "sell_side"
        touched = [lvl for lvl in ctx.m15_snapshot.equal_levels if lvl.side == want_side and abs(float(lvl.level) - target) < 1e-6]
        if touched and max((lvl.touch_count for lvl in touched), default=0) >= 3:
            score += 30.0
        elif touched:
            score += 15.0
    return max(0.0, min(100.0, score))


# ------------------------------------------------------------------------------------------
# 3. DISPLACEMENT QUALITY -- the strength of the impulsive move itself (body/ATR), distinct from
#    (1)'s break-distance measure: a break can clear the minimum distance threshold on a weak
#    candle, or a strong displacement candle can occur slightly before/after the exact break bar.
#    9:30AM's own explicit displacement-quality requirement (row: 'it should happen WITH
#    DISPLACEMENT... clumsy market structure... you will NOT be taking the trade') is the direct
#    mentor citation for this being a real, separate axis for at least one subtype.
# ------------------------------------------------------------------------------------------
def displacement_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    break_id = signal.evidence.get("structure_break_id") if signal.evidence else None
    brk = next((b for b in ctx.m15_snapshot.breaks if b.id == break_id), None) if break_id else None
    window = range(max(0, (brk.bar_index - 2)), brk.bar_index + 2) if brk else range(max(0, len(ctx.m15_rows) - 4), len(ctx.m15_rows))
    candidates = [d for d in ctx.m15_snapshot.displacements if d.bar_index in window]
    if not candidates:
        return 20.0
    strongest = max((d.magnitude_atr or 0.0) for d in candidates)
    return max(0.0, min(100.0, strongest * 35.0))


# ------------------------------------------------------------------------------------------
# 4. LOCATION QUALITY -- how deep into the premium/discount zone price actually is (0.5 exactly
#    at the boundary = weakest qualifying location, the leg's own extreme = strongest) -- the
#    mentor's own repeated 'take your entry from the MOST EXTREME zone' preference (Order Flow
#    Example), generalized from the entry-array choice to the raw zone-depth measure itself.
# ------------------------------------------------------------------------------------------
def location_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    thesis = (signal.metadata or {}).get("bsi_thesis") or {}
    low, high = thesis.get("dealing_range_low"), thesis.get("dealing_range_high")
    entry = signal.proposed_entry
    if low is None or high is None or entry is None or high <= low:
        return 50.0
    midpoint = (low + high) / 2.0
    depth = abs(entry - midpoint) / ((high - low) / 2.0)  # 0 at midpoint, 1 at the leg's own extreme
    return max(0.0, min(100.0, depth * 100.0))


# ------------------------------------------------------------------------------------------
# 5. ENTRY ARRAY QUALITY -- order block (deeper, better RR, the mentor's generally-preferred
#    array when the gap is large) scores higher than a bare FVG fill; an entry-confirmation-mode
#    trade (reactionary/liquidity_twist -- an EXTRA confirmation layer by construction) scores
#    higher still. Distinct from (4): this measures WHICH array type/confirmation was used, not
#    WHERE in the zone price sits.
# ------------------------------------------------------------------------------------------
def entry_array_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    kind = signal.evidence.get("entry_array_kind") if signal.evidence else None
    mode = signal.evidence.get("entry_confirmation_mode") if signal.evidence else "direct"
    score = 70.0 if kind == "order_block" else 50.0 if kind == "fvg" else 40.0
    if mode and mode != "direct":
        score += 20.0
    return max(0.0, min(100.0, score))


# ------------------------------------------------------------------------------------------
# 6. SESSION/TIMING QUALITY -- ny_0930's own literal clock window and asian/new_york_session's
#    own timing gates are already hard PRECONDITIONS (a rejected signal never reaches scoring at
#    all) -- this dimension instead rewards the *fakeout-size* soft quality signal the mentor
#    repeats across NY Session/Under-Over/Order Block Liquidity ('prefer small fakeouts'),
#    already computed by bsi.py::_fakeout_quality_score and carried in evidence for the
#    subtypes that have one. Neutral (50) for subtypes without a fakeout concept (order_flow/abc).
# ------------------------------------------------------------------------------------------
def session_timing_quality(signal: StrategySignal, ctx: StrategyContext) -> float:
    score = signal.evidence.get("fakeout_quality_score") if signal.evidence else None
    return float(score) if score is not None else 50.0


# Starting weights (REASONED, NOT FIT TO DATA -- see module docstring). Liquidity and structure
# weighted highest because the mentor states both as near-mandatory/hard preconditions repeatedly
# and explicitly ("if there's no liquidity you're agreeing to lose"); session/timing weighted
# lowest because it is already a hard gate for the subtypes where it applies (double-gating a
# pass/fail precondition as if it were also graded evidence would be exactly the double-counting
# Part 12 warns against) and is otherwise a soft, mentor-acknowledged-fuzzy signal for the rest.
COMPONENT_WEIGHTS: dict[str, float] = {
    "structure_quality": 0.20,
    "liquidity_quality": 0.25,
    "displacement_quality": 0.15,
    "location_quality": 0.20,
    "entry_array_quality": 0.15,
    "session_timing_quality": 0.05,
}

# UNCALIBRATED. Part 12's own explicit instruction: "determine the confidence threshold
# empirically from historical/OOS outcome bands. Do NOT automatically use 75." No OOS data was
# available in the authoring session (see bsi_final_report.md's environment-limitation
# section) -- this constant exists ONLY so `bsi_confidence_score` has a documented,
# clearly-flagged placeholder to return alongside the score, never presented as a real threshold.
DEFAULT_THRESHOLD_UNCALIBRATED: float | None = None


def bsi_confidence_score(signal: StrategySignal, ctx: StrategyContext) -> dict[str, Any]:
    """Returns the six component scores, the weighted composite, and the (uncalibrated)
    threshold placeholder -- never itself decides whether to trade; that decision belongs to
    whatever backtest/promotion process eventually calibrates DEFAULT_THRESHOLD_UNCALIBRATED
    against real OOS outcome bands per Part 14."""
    components = {
        "structure_quality": structure_quality(signal, ctx),
        "liquidity_quality": liquidity_quality(signal, ctx),
        "displacement_quality": displacement_quality(signal, ctx),
        "location_quality": location_quality(signal, ctx),
        "entry_array_quality": entry_array_quality(signal, ctx),
        "session_timing_quality": session_timing_quality(signal, ctx),
    }
    composite = sum(components[name] * weight for name, weight in COMPONENT_WEIGHTS.items())
    return {
        "components": components,
        "weights": dict(COMPONENT_WEIGHTS),
        "composite_score": round(composite, 2),
        "threshold": DEFAULT_THRESHOLD_UNCALIBRATED,
        "threshold_calibrated": False,
    }

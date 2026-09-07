"""bsi Part 13 -- mentor-aware thesis check (DESIGN + reference implementation, NOT WIRED).

Not imported by backend/adaptive_management/service.py or by any live/replay path -- this module
exists so the Part 13 "MENTOR-AWARE ADAPTIVE MANAGER (design only)" deliverable is a concrete,
testable function rather than only prose. Wiring this into the live position manager is explicitly
out of scope for this task (see the constraints in the bsi directive: "do NOT wire live
management changes").

CONTEXT (do not re-fix, already resolved on this branch): a real bug in `_position_time()`
(backend/adaptive_management/service.py) was previously found and fixed this session/branch --
it double-subtracted a broker UTC offset, landing `opened_at` ~3h in the past, which made
`_candles_held()` instantly overcount candles-held and fire TIME_EXIT within seconds of every
position. That fix is already in place; this module does not touch it and does not re-diagnose it.

THE PROBLEM THIS MODULE ADDRESSES: even with `_position_time()` fixed, Bensim's current Adaptive
Manager (`service.py::_build_management_candidates` and friends) applies UNIFORM, R-based rules
to every open position regardless of strategy (`ADAPTIVE_BREAKEVEN_R` currently 1.0,
`ADAPTIVE_PARTIAL_PROFIT_R` currently 0.5, `ADAPTIVE_TIME_EXIT_CANDLES` currently 24 candles with
`max_r < 0.25`). Part 7 row 18 and the mentor's own strategy-specific management rules show this
is a genuine mismatch for at least three bsi subtypes:
  - Asian Session mandates a FULL exit at TP, no partial-then-trail ("you must ALWAYS take all of
    your profits when the high is taken out... price can reverse heavily").
  - Under/Over and Order Block Liquidity stage PARTIAL exits specifically at intermediate FVGs
    the price passes through en route to the final target, not at a fixed R-multiple.
  - 9:30AM moves to breakeven on a FRESH STRUCTURE BREAK in the trade's favor, not at a fixed
    R-threshold ("once SOME SORT OF STRUCTURE IS BROKEN, you're gonna go BREAK EVEN").

A generic manager applying `ADAPTIVE_BREAKEVEN_R=1.0` uniformly to an Asian Session trade, or
partial-scaling an under_over trade only at a fixed R rather than at its own intermediate FVGs, is
managing a DIFFERENT thesis than the one the strategy actually earned -- exactly the "don't close
a valid mentor trade... while the structural thesis remains intact" risk the directive names.

DESIGN: every bsi signal already persists a `bsi_thesis` dict in `StrategySignal.
metadata` (see bsi_engine.py::_bsi_thesis_metadata) -- setup_subtype, structure_direction,
structural_invalidation, protected_swing, dealing_range bounds, originating_fvg_id, entry zone,
target_liquidity_level, session, and management_style. `thesis_still_intact()` below answers the
ONE question a mentor-aware manager needs before applying ANY exit rule: has the ORIGINAL
structural premise this trade was taken on been invalidated, using the SAME structural
invalidation level the strategy itself computed at entry -- not "did price move against me for
N candles" (the generic, R-based/candle-count question the current manager asks instead).
`recommended_management_action()` then dispatches to the subtype-appropriate rule the mentor
actually taught, instead of the one uniform R-based ladder.
"""
from __future__ import annotations

from typing import Any


def thesis_still_intact(bsi_thesis: dict[str, Any], *, current_price: float) -> bool:
    """False only if price has traded THROUGH the strategy's own recorded structural invalidation
    level (the protected swing the entire setup was anchored on) -- never merely because price is
    temporarily underwater or two opposing candles have printed. This is the direct, literal fix
    for the directive's own example: 'don't close a valid mentor trade after 20 seconds because of
    two opposing candles while the structural thesis remains intact'."""
    direction = bsi_thesis.get("structure_direction")
    invalidation = bsi_thesis.get("structural_invalidation")
    if direction is None or invalidation is None:
        return True  # fail-open: never invent an invalidation the thesis itself didn't record
    if direction == "bullish":
        return current_price > invalidation
    if direction == "bearish":
        return current_price < invalidation
    return True


def recommended_management_action(bsi_thesis: dict[str, Any], *, current_price: float, current_r: float | None,
                                   fresh_favorable_structure_break: bool = False, price_at_intermediate_fvg: bool = False) -> dict[str, Any]:
    """Per-subtype management dispatch (Part 13's "mentor-aware" design) -- returns an
    ILLUSTRATIVE recommendation dict, never an executable action; a real integration would still
    need to route this through service.py's own ManagementCandidate/execution plumbing, which is
    explicitly out of scope here.

    Dispatch table, each cited to its own subtype's management rule:
      - management_style == 'full_exit_at_tp' (asian_session): recommend HOLD until TP, then
        FULL_EXIT -- never a partial/trail step in between.
      - management_style == 'partial_at_intermediate_fvgs_full_at_target' (under_over): recommend
        a PARTIAL_EXIT only when `price_at_intermediate_fvg` is True, otherwise HOLD.
      - management_style == 'structure_break_breakeven_partial' (ny_0930): recommend
        MOVE_TO_BREAKEVEN only when `fresh_favorable_structure_break` is True -- never on a fixed
        R-threshold.
      - management_style == 'fixed_1_2_rr_full_exit' (new_york_session/abcd): recommend HOLD to
        the fixed 1:2 target, then FULL_EXIT -- no earlier partial per the mentor's own simpler
        description of these two subtypes.
      - management_style == 'natural_rr_no_partials' / 'natural_target_b_leg' (order_flow/abc):
        recommend HOLD to the natural/B-leg target with no partials, consistent with every
        worked example in those two subtypes never mentioning a partial.
    Every branch first checks `thesis_still_intact` -- an invalidated thesis always recommends
    FULL_EXIT (THESIS_INVALIDATED) regardless of management_style, since a broken structural
    premise is not a "let it run" situation under ANY of the mentor's own subtypes."""
    if not thesis_still_intact(bsi_thesis, current_price=current_price):
        return {"action": "FULL_EXIT", "reason": "THESIS_INVALIDATED"}

    style = bsi_thesis.get("management_style")
    if style == "full_exit_at_tp":
        return {"action": "HOLD", "reason": "asian_session_full_exit_only_at_tp"}
    if style == "partial_at_intermediate_fvgs_full_at_target":
        if price_at_intermediate_fvg:
            return {"action": "PARTIAL_EXIT", "reason": "under_over_intermediate_fvg"}
        return {"action": "HOLD", "reason": "under_over_awaiting_intermediate_fvg_or_target"}
    if style == "structure_break_breakeven_partial":
        if fresh_favorable_structure_break:
            return {"action": "MOVE_TO_BREAKEVEN", "reason": "ny_0930_structure_break_confirmed"}
        return {"action": "HOLD", "reason": "ny_0930_awaiting_confirming_structure_break"}
    if style == "fixed_1_2_rr_full_exit":
        return {"action": "HOLD", "reason": "fixed_1_2_rr_no_partials"}
    return {"action": "HOLD", "reason": "natural_target_no_partials"}

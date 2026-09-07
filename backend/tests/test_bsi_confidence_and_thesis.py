"""Part 12 (bsi_confidence.py) and Part 13 (bsi_thesis.py) design modules --
neither is wired into any live path; these tests only verify the pure functions behave as
documented so the DESIGN is at least internally correct and testable, per the directive's own
"design only... but code if time allows" instruction.
"""
from __future__ import annotations

from backend.adaptive_management.bsi_thesis import recommended_management_action, thesis_still_intact
from backend.mt5_strategies.families.bsi_confidence import COMPONENT_WEIGHTS


def test_component_weights_sum_to_one():
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_thesis_intact_for_bullish_while_price_above_invalidation():
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.1000}
    assert thesis_still_intact(thesis, current_price=1.1050) is True
    assert thesis_still_intact(thesis, current_price=1.0990) is False


def test_thesis_intact_for_bearish_while_price_below_invalidation():
    thesis = {"structure_direction": "bearish", "structural_invalidation": 1.1000}
    assert thesis_still_intact(thesis, current_price=1.0950) is True
    assert thesis_still_intact(thesis, current_price=1.1010) is False


def test_thesis_intact_fails_open_without_recorded_invalidation():
    assert thesis_still_intact({}, current_price=1.1234) is True


def test_management_action_invalidated_thesis_always_full_exits():
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.1000, "management_style": "full_exit_at_tp"}
    result = recommended_management_action(thesis, current_price=1.0900, current_r=-1.5)
    assert result == {"action": "FULL_EXIT", "reason": "THESIS_INVALIDATED"}


def test_asian_session_style_never_partials_before_tp():
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.1000, "management_style": "full_exit_at_tp"}
    result = recommended_management_action(thesis, current_price=1.1100, current_r=1.5)
    assert result["action"] == "HOLD"


def test_under_over_style_partials_only_at_intermediate_fvg():
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.1000, "management_style": "partial_at_intermediate_fvgs_full_at_target"}
    no_fvg = recommended_management_action(thesis, current_price=1.1100, current_r=1.0, price_at_intermediate_fvg=False)
    at_fvg = recommended_management_action(thesis, current_price=1.1100, current_r=1.0, price_at_intermediate_fvg=True)
    assert no_fvg["action"] == "HOLD"
    assert at_fvg["action"] == "PARTIAL_EXIT"


def test_ny_0930_style_breakeven_only_on_confirming_structure_break():
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.1000, "management_style": "structure_break_breakeven_partial"}
    no_break = recommended_management_action(thesis, current_price=1.1050, current_r=0.8, fresh_favorable_structure_break=False)
    with_break = recommended_management_action(thesis, current_price=1.1050, current_r=0.8, fresh_favorable_structure_break=True)
    assert no_break["action"] == "HOLD"
    assert with_break["action"] == "MOVE_TO_BREAKEVEN"


def test_two_opposing_candles_do_not_trigger_exit_while_thesis_intact():
    """Direct regression for the directive's own named example: a valid mentor trade must not be
    closed over two opposing candles while the structural thesis remains intact."""
    thesis = {"structure_direction": "bullish", "structural_invalidation": 1.0950, "management_style": "natural_rr_no_partials"}
    # price dipped on two opposing candles but never traded through the protected swing
    result = recommended_management_action(thesis, current_price=1.0980, current_r=-0.2)
    assert result["action"] == "HOLD"

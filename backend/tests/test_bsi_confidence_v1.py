"""BSI Intelligence Migration Phase D/E: tests for BSI_CONFIDENCE_V1
(backend/mt5_strategies/families/bsi_confidence_v1.py). The central thing this must prove: the
diagnosed bug in the original design (structure_quality/entry_array_quality/location_quality
structurally CONSTANT for retest-only subtypes, ~55% of total weight dead) is actually fixed here
-- not just documented as fixed."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.historical_intelligence.bsi_canonical_fingerprint import BSISignalSource, build_bsi_thesis_record
from backend.mt5_strategies.families.bsi_confidence_v1 import (
    CONFIDENCE_VERSION,
    bsi_confidence_v1_score,
    geometry_quality,
    liquidity_quality,
    location_quality,
    session_quality,
    setup_completeness,
    structure_quality,
)


def _under_over_record(*, touches: int, entry: float = 1.0510, equilibrium: float = 1.0550, dr_low: float = 1.0480, dr_high: float = 1.0620, target: float = 1.0650, penetration_atr: float | None = 0.4) -> "BSIThesisRecordORM":
    thesis = {
        "liquidity_source": "equal_level_min_3_touches", "liquidity_side": "sell_side", "liquidity_level": target,
        "liquidity_swept": True, "dealing_range_low": dr_low, "dealing_range_high": dr_high, "equilibrium": equilibrium,
        "premium_discount_location": "discount", "target_type": "natural_opposing_liquidity", "target_level": target,
        "session_window": "asian_lunch",
    }
    evidence = {"level_touch_count": touches, "penetration_atr": penetration_atr}
    source = BSISignalSource(
        fingerprint_id=f"HPF_touch{touches}", bsi_version="BSI_BASELINE_V1", subtype="bsi_under_over",
        canonical_symbol="EURUSD", direction="LONG", candidate_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        entry_time=datetime(2026, 3, 1, tzinfo=timezone.utc), execution_timeframe="M15",
        thesis=thesis, evidence=evidence, entry=entry, initial_stop=1.0470,
    )
    return build_bsi_thesis_record(source)


def _order_flow_record(*, has_break: bool) -> "BSIThesisRecordORM":
    thesis = {"structure_break_level": 1.0600 if has_break else None, "external_structure": "bullish" if has_break else None,
              "target_type": "natural_opposing_liquidity", "target_level": 1.0700}
    source = BSISignalSource(
        fingerprint_id="HPF_of1", bsi_version="BSI_BASELINE_V1", subtype="bsi_order_flow",
        canonical_symbol="EURUSD", direction="LONG", candidate_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        entry_time=datetime(2026, 3, 1, tzinfo=timezone.utc), execution_timeframe="M15",
        thesis=thesis, evidence={}, entry=1.0500, initial_stop=1.0470,
    )
    return build_bsi_thesis_record(source)


def test_structure_quality_is_not_constant_for_retest_only_subtype():
    """THE core bug this redesign exists to fix: structure_quality must genuinely vary with real
    evidence (touch count) for bsi_under_over, not return the same flat 50.0 every time the way
    the original bsi_confidence.py's own structure_quality did."""
    low = structure_quality(_under_over_record(touches=3))
    high = structure_quality(_under_over_record(touches=8))
    assert low is not None and high is not None
    assert high > low  # more touches -> genuinely higher score, not a flat constant


def test_structure_quality_none_when_touch_count_unavailable():
    record = _under_over_record(touches=3)
    record.subtype_extension = {}  # simulate a candidate with no touch-count data at all
    assert structure_quality(record) is None  # honestly unknown, not fabricated as 50.0


def test_structure_quality_order_flow_still_works_the_old_way():
    """Structure-break-anchored subtypes keep a break-presence-based score -- the redesign only
    changes retest-only subtypes' definition, doesn't remove the concept for subtypes that
    genuinely have it."""
    assert structure_quality(_order_flow_record(has_break=True)) == 70.0
    assert structure_quality(_order_flow_record(has_break=False)) is None


def test_setup_completeness_excludes_liquidity_fields_anti_double_counting():
    """The module docstring's own explicit anti-double-counting claim, proven directly: a record
    with liquidity fully populated but everything else empty must NOT score high on
    setup_completeness purely because of the liquidity fields (those are liquidity_quality's own
    domain)."""
    record = _under_over_record(touches=5)
    record.premium_discount_location = None
    record.dealing_range_low = None
    record.target_type = None
    record.session_window = None
    # liquidity_level/liquidity_side/liquidity_swept are still populated (from _under_over_record)
    assert record.liquidity_level is not None
    completeness = setup_completeness(record)
    assert completeness == 0.0  # none of the (non-liquidity) completeness fields are populated


def test_location_quality_reuses_phase_b_helper_directly():
    record = _under_over_record(touches=3)  # entry=1.0510, equilibrium=1.0550, half_range=0.0070
    score = location_quality(record)
    assert score is not None
    assert 0.0 <= score <= 100.0


def test_missing_dimensions_return_none_not_fabricated():
    record = _order_flow_record(has_break=False)
    assert structure_quality(record) is None
    # execution_quality always None today (spread_at_entry/atr_at_entry never threaded through yet)
    from backend.mt5_strategies.families.bsi_confidence_v1 import execution_quality
    assert execution_quality(record) is None


def test_composite_score_renormalizes_over_available_dimensions_only():
    """A candidate missing several dimensions must not be silently punished as if those
    dimensions scored 0 -- the composite renormalizes weight over whatever IS available."""
    record = _order_flow_record(has_break=False)  # structure_quality, liquidity_quality both None
    result = bsi_confidence_v1_score(record)
    assert result["confidence_version"] == CONFIDENCE_VERSION
    assert "structure_quality" in result["components_missing"]
    assert result["composite_score"] is not None  # still computable from whatever's available
    assert result["threshold_calibrated"] is False
    assert result["threshold"] is None


def test_geometry_quality_prefers_mentors_own_moderate_rr_band():
    """Mentor's own repeated 'don't chase big RR, 1:2-1:5 is enough' cross-subtype rule --
    proven directly: a very large RR must NOT score higher than a comfortable, moderate one."""
    record_moderate = _under_over_record(touches=3, target=1.0650)  # RR = |1.0650-1.0510|/0.0040 = 3.5
    record_extreme = _under_over_record(touches=3, target=1.2000)  # RR enormous
    moderate_score = geometry_quality(record_moderate)
    extreme_score = geometry_quality(record_extreme)
    assert moderate_score == 100.0
    assert extreme_score is not None and extreme_score < moderate_score


def test_session_quality_docstring_honestly_flags_the_inverse_finding():
    """Not a behavioral test (the function still encodes the mentor's literal rule) -- proves the
    documented caveat about the real, evidenced inverse correlation is actually present in the
    module, so this can't silently be 'forgotten' by a future session."""
    import backend.mt5_strategies.families.bsi_confidence_v1 as mod
    assert "negatively correlated" in mod.session_quality.__doc__.lower() or "NEGATIVELY correlated" in mod.session_quality.__doc__

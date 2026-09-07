"""BSI Intelligence Migration Phase D/H: tests for the BSI HI fingerprint contract
(backend/historical_intelligence/bsi_hi_fingerprint.py)."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.historical_intelligence.bsi_canonical_fingerprint import BSISignalSource, build_bsi_thesis_record
from backend.historical_intelligence.bsi_hi_fingerprint import (
    BSI_MIN_EFFECTIVE_SAMPLE,
    BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL,
    bsi_candidate_passes_hard_filters,
    bsi_hard_filter_dims,
    bsi_hi_result_from_neighbor_stats,
    bsi_similarity_dims,
    bsi_similarity_score,
)
from backend.historical_intelligence.entry_intelligence import (
    HIST_INTEL_DEFER,
    HIST_INTEL_INSUFFICIENT,
    HIST_INTEL_NEUTRAL,
    HIST_INTEL_RANK_ADJUST,
    HIST_INTEL_REJECT,
    HIST_INTEL_SUPPORT,
)


def _record(*, subtype="bsi_under_over", symbol="EURUSD", direction="LONG", session="asian", pd="discount") -> "BSIThesisRecordORM":
    thesis = {"structure_direction": "bullish", "premium_discount_location": pd, "liquidity_side": "sell_side",
              "session": session, "target_type": "natural_opposing_liquidity", "liquidity_swept": True}
    source = BSISignalSource(
        fingerprint_id=f"HPF_{subtype}_{symbol}", bsi_version="BSI_BASELINE_V1", subtype=subtype,
        canonical_symbol=symbol, direction=direction, candidate_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        entry_time=datetime(2026, 3, 1, tzinfo=timezone.utc), execution_timeframe="M15",
        thesis=thesis, evidence={}, entry=1.05, initial_stop=1.048,
    )
    return build_bsi_thesis_record(source)


def test_identical_records_score_perfect_similarity():
    a = bsi_similarity_dims(_record())
    b = bsi_similarity_dims(_record())
    assert bsi_similarity_score(a, b) == 1.0


def test_partial_mismatch_scores_between_zero_and_one():
    a = bsi_similarity_dims(_record(session="asian", pd="discount"))
    b = bsi_similarity_dims(_record(session="new_york", pd="discount"))  # session differs, PD location matches
    score = bsi_similarity_score(a, b)
    assert 0.0 < score < 1.0


def test_similarity_never_penalizes_a_dimension_the_query_does_not_know():
    query = {"structure_direction": "bullish"}  # only one dimension known
    candidate = bsi_similarity_dims(_record())
    score = bsi_similarity_score(query, candidate)
    assert score == 1.0  # the one known dimension matches -> perfect score, no penalty for the rest being unset


def test_hard_filters_reject_mismatched_subtype_or_symbol():
    a = bsi_hard_filter_dims(_record(subtype="bsi_under_over", symbol="EURUSD"))
    b = bsi_hard_filter_dims(_record(subtype="bsi_new_york", symbol="EURUSD"))
    assert bsi_candidate_passes_hard_filters(a, b) is False
    c = bsi_hard_filter_dims(_record(subtype="bsi_under_over", symbol="GBPUSD"))
    assert bsi_candidate_passes_hard_filters(a, c) is False
    d = bsi_hard_filter_dims(_record(subtype="bsi_under_over", symbol="EURUSD"))
    assert bsi_candidate_passes_hard_filters(a, d) is True


def test_insufficient_sample_below_minimum_effective_sample():
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE - 1, mean_r=1.0, win_rate=0.7)
    assert result["hi_result"] == HIST_INTEL_INSUFFICIENT


def test_strong_positive_evidence_with_reliable_sample_supports():
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL + 10, mean_r=0.6, win_rate=0.65)
    assert result["hi_result"] == HIST_INTEL_SUPPORT


def test_strong_positive_evidence_but_thin_sample_only_rank_adjusts_not_support():
    """Directive's own explicit requirement: effective sample size gates how MUCH weight positive
    evidence gets, not just whether it exists at all."""
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE + 1, mean_r=0.6, win_rate=0.65)
    assert result["hi_result"] == HIST_INTEL_RANK_ADJUST
    assert result["hi_result"] != HIST_INTEL_SUPPORT


def test_strong_negative_evidence_with_reliable_sample_rejects():
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL + 10, mean_r=-0.5, win_rate=0.2)
    assert result["hi_result"] == HIST_INTEL_REJECT


def test_strong_negative_evidence_but_thin_sample_only_defers():
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE + 1, mean_r=-0.5, win_rate=0.2)
    assert result["hi_result"] == HIST_INTEL_DEFER


def test_neutral_expectancy_with_reliable_sample():
    result = bsi_hi_result_from_neighbor_stats(effective_sample_size=BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL + 10, mean_r=0.05, win_rate=0.5)
    assert result["hi_result"] == HIST_INTEL_NEUTRAL


def test_bsi_dimension_weights_never_touches_the_shared_generic_table():
    """Proves this module's own weight table is genuinely separate from similarity.py's shared,
    cross-strategy DIMENSION_WEIGHTS -- the whole point of not corrupting shared infra."""
    from backend.historical_intelligence.bsi_hi_fingerprint import BSI_DIMENSION_WEIGHTS
    from backend.historical_intelligence.similarity import DIMENSION_WEIGHTS

    assert BSI_DIMENSION_WEIGHTS is not DIMENSION_WEIGHTS
    # BSI's own dimensions (structure_direction, premium_discount_location, target_type) must not
    # exist in the generic table -- confirming they were genuinely added here, not to the shared one.
    assert "structure_direction" not in DIMENSION_WEIGHTS
    assert "premium_discount_location" not in DIMENSION_WEIGHTS
    assert "target_type" not in DIMENSION_WEIGHTS

"""Adaptive Manager multi-neighbor analog intelligence (section 17-19 of the analog-intelligence
directive): the SAME weighted multi-neighbor idea similarity.py applies to entry candidates,
applied instead to OPEN TRADE STATES.

Reuses adaptive_statistics.py's existing exact-peer-group machinery for its data access
(_collect_rows -- real AdaptiveManagementEventORM snapshots joined to
AdaptiveManagerCounterfactualORM's post-exit resolution) and adaptive_fingerprint.py's existing
state-fingerprint field taxonomy -- this module adds WEIGHTED ranking and effective-sample-size
statistics over that SAME real data, exactly like similarity.py added a weighted layer on top of
statistics.py's exact peer_group_hash aggregation, without duplicating either.

Hard-match: strategy, symbol, direction (a trade's evolving state is never blended across a
different strategy/symbol/direction). Weighted dimensions: current_regime, mfe_bucket,
current_r_bucket, elapsed_bucket, session, be_state -- the "how far along, in what context" state
description build_state_fingerprint already buckets.
"""
from __future__ import annotations

import statistics as pystats
from typing import Any

STATE_DIMENSION_WEIGHTS: dict[str, float] = {
    "current_regime": 2.0,
    "mfe_bucket": 2.0,
    "current_r_bucket": 1.5,
    "elapsed_bucket": 1.0,
    "session": 1.0,
    "be_state": 1.0,
}
_MIN_SIMILARITY_THRESHOLD = 0.6
_DEFAULT_TOP_K = 50


def state_similarity_score(query: dict[str, Any], candidate: dict[str, Any]) -> float:
    """Pure function -- identical formula to similarity.similarity_score, applied to
    STATE_DIMENSION_WEIGHTS instead of entry-side DIMENSION_WEIGHTS."""
    available_weight = 0.0
    matched_weight = 0.0
    for dim, weight in STATE_DIMENSION_WEIGHTS.items():
        q_val = query.get(dim)
        if q_val is None:
            continue
        available_weight += weight
        if candidate.get(dim) == q_val:
            matched_weight += weight
    if available_weight <= 0:
        return 0.0
    return round(matched_weight / available_weight, 4)


def find_similar_states(
    *, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any],
    min_similarity: float = _MIN_SIMILARITY_THRESHOLD, top_k: int = _DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    """Hard-filters real adaptive trade-state rows (adaptive_statistics._collect_rows -- the SAME
    real AdaptiveManagementEventORM+AdaptiveManagerCounterfactualORM data the exact
    state_statistics already reads) to (strategy, symbol, direction) and RESOLVED post-exit
    outcomes only, then ranks by state_similarity_score. Returns [] (never forces a match) when
    nothing clears the bar."""
    from backend.historical_intelligence.adaptive_statistics import _collect_rows

    rows = _collect_rows()
    neighbors = []
    for row in rows:
        fields = row["fields"]
        if fields.get("strategy") != strategy or fields.get("symbol") != symbol or fields.get("direction") != direction:
            continue
        counterfactual = row["counterfactual"]
        if counterfactual is None or counterfactual.post_exit_status != "RESOLVED":
            continue
        score = state_similarity_score(query_fields, fields)
        if score < min_similarity:
            continue
        neighbors.append({"fields": fields, "counterfactual": counterfactual, "similarity": score})

    neighbors.sort(key=lambda n: -n["similarity"])
    return neighbors[:top_k]


def _weighted_probability(neighbors: list[dict[str, Any]], attr: str) -> float | None:
    weighted = [(n["similarity"], getattr(n["counterfactual"], attr)) for n in neighbors if getattr(n["counterfactual"], attr) is not None]
    total = sum(w for w, _ in weighted)
    if total <= 0:
        return None
    return round(sum(w * (1.0 if v else 0.0) for w, v in weighted) / total, 4)


def weighted_state_statistics(*, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any], top_k: int = _DEFAULT_TOP_K) -> dict[str, Any]:
    """The Adaptive Manager counterpart to similarity.similarity_statistics: weighted P(continue)/
    P(reversal)/P(round-trip)/expected_additional_R across MANY similar real trade-state
    evolutions, never a single nearest match. effective_sample_size is the sum of similarity
    weights -- same conservative property as the entry-side model (never inflates a raw count)."""
    from backend.historical_intelligence.statistics import reliability_label

    neighbors = find_similar_states(strategy=strategy, symbol=symbol, direction=direction, query_fields=query_fields, top_k=top_k)
    if not neighbors:
        return {"status": "NO_GOOD_HISTORICAL_ANALOG", "raw_neighbor_count": 0, "effective_sample_size": 0.0, "reliability": reliability_label(0)}

    weights = [n["similarity"] for n in neighbors]
    effective_sample_size = round(sum(weights), 2)

    reach_1r = _weighted_probability(neighbors, "post_exit_reached_plus_1r")
    reversed_strongly = _weighted_probability(neighbors, "post_exit_reversed_strongly")
    round_trip_pairs = [(n["similarity"], bool(n["counterfactual"].post_exit_reached_plus_1r) and bool(n["counterfactual"].post_exit_reversed_strongly)) for n in neighbors if n["counterfactual"].post_exit_reached_plus_1r is not None and n["counterfactual"].post_exit_reversed_strongly is not None]
    round_trip_weight = sum(w for w, _ in round_trip_pairs)
    probability_round_trip = round(sum(w * (1.0 if v else 0.0) for w, v in round_trip_pairs) / round_trip_weight, 4) if round_trip_weight > 0 else None

    additional_r_pairs = [(n["similarity"], n["counterfactual"].post_exit_additional_r_available) for n in neighbors if n["counterfactual"].post_exit_additional_r_available is not None]
    additional_r_weight = sum(w for w, _ in additional_r_pairs)
    expected_additional_r = round(sum(w * v for w, v in additional_r_pairs) / additional_r_weight, 4) if additional_r_weight > 0 else None

    return {
        "status": "OK",
        "raw_neighbor_count": len(neighbors),
        "effective_sample_size": effective_sample_size,
        "reliability": reliability_label(int(effective_sample_size)),
        "median_similarity": round(pystats.median(weights), 4),
        "probability_reach_plus_1r": reach_1r,
        "probability_reversal": reversed_strongly,
        "probability_round_trip": probability_round_trip,
        "expected_additional_r": expected_additional_r,
    }


# Existing Adaptive Manager action taxonomy only -- never invented here.
RECOMMENDATION_HOLD = "HOLD"
RECOMMENDATION_LIGHT_PROTECTION = "LIGHT_PROTECTION"
RECOMMENDATION_PROTECT_TRAIL_EXIT = "PROTECT_TRAIL_EXIT"
RECOMMENDATION_INSUFFICIENT = "HIST_INTEL_INSUFFICIENT"


def historical_management_recommendation(stats: dict[str, Any], *, min_effective_sample: int = 20) -> str:
    """Deterministic, transparent -- no one-metric rule (mirrors entry_intelligence.py's
    _historical_decision). Never selects an action outside HOLD/LIGHT_PROTECTION/
    PROTECT_TRAIL_EXIT -- the caller maps these onto the Adaptive Manager's OWN existing action
    types (MOVE_SL_TO_REDUCED_RISK/TRAIL/MFE_PROTECTION_CLOSE/etc.); this function never invents
    a new action type."""
    if stats.get("status") != "OK" or (stats.get("effective_sample_size") or 0) < min_effective_sample:
        return RECOMMENDATION_INSUFFICIENT
    round_trip = stats.get("probability_round_trip")
    reversal = stats.get("probability_reversal")
    expected_additional_r = stats.get("expected_additional_r")
    if (round_trip is not None and round_trip >= 0.45) or (reversal is not None and reversal >= 0.5) or (expected_additional_r is not None and expected_additional_r < 0):
        return RECOMMENDATION_PROTECT_TRAIL_EXIT
    if expected_additional_r is not None and expected_additional_r > 0 and (stats.get("probability_reach_plus_1r") or 0) >= 0.5:
        return RECOMMENDATION_HOLD
    return RECOMMENDATION_LIGHT_PROTECTION

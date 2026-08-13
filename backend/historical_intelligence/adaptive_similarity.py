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
from datetime import timedelta
from typing import Any

# Adaptive-Historical-Intelligence-Backfill directive, Phase 7's explicit weighting hierarchy.
# Hard-match dimensions (strategy/symbol/direction) are enforced separately in
# find_similar_states, never here -- this dict is only ever the WEIGHTED layer on top of that
# hard filter. giveback_bucket/structure_intact/structure_against_trade/atr_regime are new
# (Phase 6/7); current_regime/mfe_bucket/current_r_bucket/elapsed_bucket/session/be_state are the
# original Part 15/17 set, unchanged.
STATE_DIMENSION_WEIGHTS: dict[str, float] = {
    "current_regime": 2.0,
    "mfe_bucket": 2.0,
    "current_r_bucket": 1.5,
    "giveback_bucket": 1.5,
    "session": 1.0,
    "structure_intact": 1.2,
    "structure_against_trade": 1.2,
    "elapsed_bucket": 1.0,
    "be_state": 1.0,
    "atr_regime": 0.75,
}
ADAPTIVE_SIMILARITY_MODEL_VERSION = "adaptive-sim-v1"
_MIN_SIMILARITY_THRESHOLD = 0.6
_DEFAULT_TOP_K = 50
_SAME_TRADE_DEDUP = True
_CLUSTER_WINDOW = timedelta(hours=6)  # mirrors similarity.py's entry-side temporal-cluster window


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


def _cluster_and_dedup_states(neighbors: list[dict[str, Any]], *, window: timedelta = _CLUSTER_WINDOW) -> list[dict[str, Any]]:
    """Adaptive-state counterpart to similarity.cluster_and_dedup (Phase 4 -- "states from the
    same trade remain correlated... states from the same contiguous market move remain
    correlated... do not inflate statistical confidence"). Two passes, both strictly reducing,
    never inflating, the candidate list:
      1. Same-trade dedup: multiple checkpoint states from the SAME originating trade (identified
         by trade_id -- a real position_id or a historical source_fingerprint_id) collapse to
         only their single highest-similarity representative. Necessary because a query can
         legitimately match several checkpoints of ONE trade (e.g. both its 0.5R and 0.75R
         states can fall in the same current_r_bucket) -- counting that trade twice would
         overstate independent evidence.
      2. Temporal-cluster dedup: among DIFFERENT trades, those sharing (symbol, direction,
         strategy) whose state_time falls within `window` of an already-kept trade collapse to
         the single best representative -- the same contiguous market move producing several
         "different" trades that are not statistically independent."""
    if not neighbors:
        return neighbors

    best_by_trade: dict[str, dict[str, Any]] = {}
    for n in neighbors:
        trade_id = n.get("trade_id")
        if trade_id is None:
            best_by_trade[id(n)] = n
            continue
        existing = best_by_trade.get(trade_id)
        if existing is None or n["similarity"] > existing["similarity"]:
            best_by_trade[trade_id] = n
    deduped_by_trade = sorted(best_by_trade.values(), key=lambda n: -n["similarity"])

    kept: list[dict[str, Any]] = []
    kept_windows: list[tuple[str, str, str, Any]] = []  # (symbol, direction, strategy, state_time)
    for n in deduped_by_trade:
        fields = n["fields"]
        key = (fields.get("symbol"), fields.get("direction"), fields.get("strategy"))
        state_time = n.get("state_time")
        clustered = False
        if state_time is not None:
            for sym, dirn, strat, t in kept_windows:
                if (sym, dirn, strat) == key and abs((state_time - t).total_seconds()) <= window.total_seconds():
                    clustered = True
                    break
        if clustered:
            continue
        kept.append(n)
        if state_time is not None:
            kept_windows.append((key[0], key[1], key[2], state_time))
    return kept


def find_similar_states(
    *, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any],
    min_similarity: float = _MIN_SIMILARITY_THRESHOLD, top_k: int = _DEFAULT_TOP_K,
    include_historical: bool = True, dedup: bool = True,
) -> list[dict[str, Any]]:
    """Hard-filters adaptive trade-state rows to (strategy, symbol, direction) and RESOLVED
    post-exit outcomes only, then ranks by state_similarity_score. Draws from TWO sources,
    merged (Phase 7 -- "reuse the existing multi-neighbor architecture, do not duplicate"):
      1. Real managed-position evolutions (adaptive_statistics._collect_rows) -- the ORIGINAL
         source this function always had.
      2. Historical backfilled trade-state checkpoints (adaptive_statistics.
         _collect_historical_rows, built by adaptive_backfill.py from the entry-side replay
         corpus) -- NEW, gives this function evidence volume real DEMO-managed positions alone
         (currently ~30 resolved) cannot yet provide.
    Both sources are scored through the IDENTICAL state_similarity_score/dedup pipeline -- a
    historical neighbor is never treated as inherently weaker evidence than a real one; only
    resolution quality (RESOLVED-only) and similarity determine ranking. Returns [] (never
    forces a match) when nothing clears the bar."""
    scored = _gather_and_score_neighbors(strategy=strategy, symbol=symbol, direction=direction, query_fields=query_fields, min_similarity=min_similarity, include_historical=include_historical)
    neighbors = scored
    if dedup:
        neighbors = _cluster_and_dedup_states(neighbors)
        neighbors.sort(key=lambda n: -n["similarity"])
    return neighbors[:top_k]


def _gather_and_score_neighbors(
    *, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any],
    min_similarity: float, include_historical: bool,
) -> list[dict[str, Any]]:
    """The actual DB-collection + scoring work find_similar_states does -- split out so
    weighted_state_statistics can call it ONCE and derive both the raw (pre-dedup) and
    independent (post-dedup) neighbor views from the SAME in-memory list, instead of re-running
    the full Postgres collection twice per evaluation (a real, measured cost: this collection
    step is the dominant remaining latency after Part 21's query-bounding fix)."""
    from backend.historical_intelligence.adaptive_statistics import _collect_historical_rows, _collect_rows

    real_rows = _collect_rows()
    historical_rows = _collect_historical_rows(strategy=strategy, symbol=symbol, direction=direction) if include_historical else []

    neighbors = []
    for row in real_rows:
        fields = row["fields"]
        if fields.get("strategy") != strategy or fields.get("symbol") != symbol or fields.get("direction") != direction:
            continue
        counterfactual = row["counterfactual"]
        if counterfactual is None or counterfactual.post_exit_status != "RESOLVED":
            continue
        score = state_similarity_score(query_fields, fields)
        if score < min_similarity:
            continue
        event = row.get("event")
        neighbors.append({
            "fields": fields, "counterfactual": counterfactual, "similarity": score,
            "trade_id": f"REAL:{event.position_id}" if event is not None else None,
            "state_time": getattr(event, "created_at", None), "source": "REAL",
        })

    for row in historical_rows:
        fields = row["fields"]
        counterfactual = row["counterfactual"]
        if counterfactual is None or counterfactual.post_exit_status != "RESOLVED":
            continue
        score = state_similarity_score(query_fields, fields)
        if score < min_similarity:
            continue
        neighbors.append({
            "fields": fields, "counterfactual": counterfactual, "similarity": score,
            "trade_id": f"HIST:{row['source_trade_id']}", "state_time": row.get("state_time"), "source": "HISTORICAL",
        })

    neighbors.sort(key=lambda n: -n["similarity"])
    return neighbors


def _weighted_probability(neighbors: list[dict[str, Any]], attr: str) -> float | None:
    """getattr(..., None) deliberately, not a bare attribute access: some fields (e.g.
    reached_plus_1_5r_additional/round_trip_to_breakeven) exist only on
    HistoricalAdaptiveOutcomeORM, not on the REAL AdaptiveManagerCounterfactualORM schema -- a
    real-sourced neighbor simply doesn't contribute to that specific weighted probability rather
    than raising, since the two sources are merged and scored through this same function."""
    weighted = [(n["similarity"], v) for n in neighbors if (v := getattr(n["counterfactual"], attr, None)) is not None]
    total = sum(w for w, _ in weighted)
    if total <= 0:
        return None
    return round(sum(w * (1.0 if v else 0.0) for w, v in weighted) / total, 4)


def _weighted_prob_attr(neighbors: list[dict[str, Any]], attr: str) -> float | None:
    return _weighted_probability(neighbors, attr)


def weighted_state_statistics(*, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any], top_k: int = _DEFAULT_TOP_K) -> dict[str, Any]:
    """The Adaptive Manager counterpart to similarity.similarity_statistics: weighted P(continue)/
    P(reversal)/P(round-trip)/expected_additional_R across MANY similar trade-state evolutions
    (real AND historical, merged -- see find_similar_states), never a single nearest match.
    effective_sample_size is the sum of similarity weights -- same conservative property as the
    entry-side model (never inflates a raw count). Reports raw_neighbor_count (post-hard-filter,
    PRE-dedup -- how many candidate rows existed at all) separately from
    independent_neighbor_count (post same-trade/temporal-cluster dedup -- Phase 4's explicit
    "do not inflate statistical confidence" requirement) so a caller can see both how much
    evidence exists and how much of it is actually independent."""
    from backend.historical_intelligence.statistics import reliability_label

    scored = _gather_and_score_neighbors(strategy=strategy, symbol=symbol, direction=direction, query_fields=query_fields, min_similarity=_MIN_SIMILARITY_THRESHOLD, include_historical=True)
    raw_neighbors = scored[:top_k]
    neighbors = _cluster_and_dedup_states(scored)
    neighbors.sort(key=lambda n: -n["similarity"])
    neighbors = neighbors[:top_k]
    if not neighbors:
        return {"status": "NO_GOOD_HISTORICAL_ANALOG", "raw_neighbor_count": len(raw_neighbors), "independent_neighbor_count": 0, "effective_sample_size": 0.0, "reliability": reliability_label(0)}

    weights = [n["similarity"] for n in neighbors]
    effective_sample_size = round(sum(weights), 2)

    reach_1r = _weighted_prob_attr(neighbors, "post_exit_reached_plus_1r")
    reach_1_5r = _weighted_prob_attr(neighbors, "reached_plus_1_5r_additional")
    reach_2r = _weighted_prob_attr(neighbors, "reached_plus_2r_additional")
    reach_tp = _weighted_prob_attr(neighbors, "post_exit_reached_original_tp")
    hit_sl = _weighted_prob_attr(neighbors, "post_exit_would_have_hit_original_sl")
    reversed_strongly = _weighted_prob_attr(neighbors, "post_exit_reversed_strongly")
    round_trip_be = _weighted_prob_attr(neighbors, "round_trip_to_breakeven")
    round_trip_loss_hist = _weighted_prob_attr(neighbors, "round_trip_to_loss")

    round_trip_pairs = [(n["similarity"], bool(n["counterfactual"].post_exit_reached_plus_1r) and bool(n["counterfactual"].post_exit_reversed_strongly)) for n in neighbors if n["counterfactual"].post_exit_reached_plus_1r is not None and n["counterfactual"].post_exit_reversed_strongly is not None]
    round_trip_weight = sum(w for w, _ in round_trip_pairs)
    probability_round_trip = round(sum(w * (1.0 if v else 0.0) for w, v in round_trip_pairs) / round_trip_weight, 4) if round_trip_weight > 0 else round_trip_loss_hist

    additional_r_pairs = [(n["similarity"], n["counterfactual"].post_exit_additional_r_available) for n in neighbors if n["counterfactual"].post_exit_additional_r_available is not None]
    additional_r_weight = sum(w for w, _ in additional_r_pairs)
    expected_additional_r = round(sum(w * v for w, v in additional_r_pairs) / additional_r_weight, 4) if additional_r_weight > 0 else None

    real_count = sum(1 for n in neighbors if n.get("source") == "REAL")
    historical_count = sum(1 for n in neighbors if n.get("source") == "HISTORICAL")

    return {
        "status": "OK",
        "raw_neighbor_count": len(raw_neighbors),
        "independent_neighbor_count": len(neighbors),
        "real_neighbor_count": real_count, "historical_neighbor_count": historical_count,
        "effective_sample_size": effective_sample_size,
        "reliability": reliability_label(int(effective_sample_size)),
        "median_similarity": round(pystats.median(weights), 4),
        "min_similarity": round(min(weights), 4), "max_similarity": round(max(weights), 4),
        "probability_reach_plus_1r": reach_1r,
        "probability_reach_plus_1_5r": reach_1_5r,
        "probability_reach_plus_2r": reach_2r,
        "probability_original_tp": reach_tp,
        "probability_hit_sl": hit_sl,
        "probability_reversal": reversed_strongly,
        "probability_round_trip": probability_round_trip,
        "probability_round_trip_to_breakeven": round_trip_be,
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

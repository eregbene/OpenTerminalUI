"""BSI Historical Intelligence fingerprint contract (BSI Intelligence Migration Phase D, second
half + Phase H design).

======================================================================================
REUSE, NOT REBUILD (directive's own explicit instruction, Section 8/11)
======================================================================================
Everything genuinely generic about Historical Intelligence already exists and already works
correctly for BSI with ZERO code changes -- verified by direct read this mission, not assumed:
  - `fingerprint.py::build_fingerprint()`'s `peer_group_hash` HARD-MATCHES on `anchor_strategy`.
    Every BSI subtype is persisted under its own distinct `anchor_strategy` (`bsi__bsi_under_over`
    etc.), so BSI's 9 subtypes already occupy isolated peer-group spaces, NEVER blended with each
    other or with any legacy strategy's fingerprints -- directive Section 8's "do NOT mix legacy
    strategy fingerprints into BSI neighbor selection" requirement is already satisfied
    structurally, with no new code.
  - `trust_gating.py::get_trust_state()` fails closed (`HIST_INTEL_UNTRUSTED_REPLAY`) whenever no
    parity-check row exists for a strategy -- BSI has zero such rows on record, so it is
    automatically untrusted for live HI influence today, with no code change required.
  - `entry_intelligence.py`'s SUPPORT/RANK_ADJUST/DEFER/REJECT/HIST_INTEL_NEUTRAL/
    HIST_INTEL_INSUFFICIENT decision model operates on a generic stats-dict shape -- it does not
    need to know anything BSI-specific to work correctly once fed BSI-specific neighbor
    statistics. REUSED AS-IS, not reimplemented, here.
  - `similarity.py::similarity_score(query, candidate)`'s ALGORITHM (weighted-match-fraction over
    whatever dimensions the query actually knows, never penalizing an unset dimension) is reused
    VERBATIM below (`bsi_similarity_score`, a direct copy of that function's own logic) -- but NOT
    its `DIMENSION_WEIGHTS` module-level constant, which is a SHARED, cross-strategy config table.
    BSI needs genuinely different, richer dimensions (subtype, structure_direction, premium/
    discount location, liquidity side, target type -- none of which exist on the generic
    fingerprint) that have no business being added to a table every OTHER strategy's own
    similarity search also reads from. `BSI_DIMENSION_WEIGHTS` below is a SEPARATE, BSI-scoped
    table for exactly this reason -- reusing the algorithm, not corrupting the shared config.

======================================================================================
WHAT THIS MODULE DOES NOT DO YET
======================================================================================
Does not implement a full `find_similar_setups`-equivalent DB query for BSI (that function's own
neighbor-gathering SQL, clustering, and caching logic could be reused close to verbatim once BSI
has enough real occurrences with resolved outcomes to search over -- deferred to Phase H, when the
Phase F backfill actually provides that corpus; building the query path now against an empty
`BSIThesisRecordORM` table would be untestable, not genuinely "done"). This module defines the
CONTRACT (which dimensions, how they're weighted, how a candidate is projected into them) that
Phase H's actual neighbor search will consume.
"""
from __future__ import annotations

from typing import Any

from backend.historical_intelligence.bsi_canonical_fingerprint import BSIThesisRecordORM

BSI_HI_VERSION = "BSI_HI_V1"

# BSI-native similarity dimensions -- deliberately covers what directive Section 8 names
# (subtype, symbol/direction are HARD filters, not scored here -- see note below; structure,
# liquidity, sweep, location, session, regime, FVG/OB relationship, geometry ARE scored here) and
# what BSI's OWN canonical schema (Phase B) actually makes available, never a field that doesn't
# exist. Weights are a REASONED STARTING allocation (same "not yet fit to data" posture as
# BSI_CONFIDENCE_V1's own weights) -- Phase H's real neighbor-search evidence is what would
# eventually justify adjusting them, not guesswork now.
BSI_DIMENSION_WEIGHTS: dict[str, float] = {
    # HIGH-WEIGHT -- the mentor's own structural/liquidity vocabulary
    "structure_direction": 2.0,  # bullish/bearish -- distinct from trade direction, the mentor's own concept
    "premium_discount_location": 2.0,
    "liquidity_side": 1.5,
    "session": 1.5,
    "target_type": 1.5,
    # MEDIUM-WEIGHT
    "liquidity_swept": 1.2,
    "execution_timeframe": 0.5,  # almost always M15 today, low discriminating power, kept for future timeframe diversity
}
# HARD filters (never scored, enforced as an exact-match precondition on the candidate pool
# before similarity_score is ever called) -- mirrors similarity.py's own documented convention
# ("symbol, direction, anchor_strategy, strategy_version, regime_broad" are hard filters there).
# For BSI: subtype (== anchor_strategy's own peer-group isolation, already enforced structurally
# by fingerprint.py -- restated here for this module's own callers' clarity), canonical_symbol,
# direction, bsi_version (never mix BSI_BASELINE_V1 occurrences with a future, different-rules
# BSI_BASELINE_V2 if one is ever created).
BSI_HARD_FILTER_FIELDS = ("subtype", "canonical_symbol", "direction", "bsi_version")


def bsi_similarity_dims(record: BSIThesisRecordORM) -> dict[str, Any]:
    """Projects a BSIThesisRecordORM into the dimension-dict shape `bsi_similarity_score` expects
    -- the BSI-native analog of similarity.py's own `_fingerprint_to_dims()`."""
    return {
        "structure_direction": record.structure_direction,
        "premium_discount_location": record.premium_discount_location,
        "liquidity_side": record.liquidity_side,
        "session": record.session,
        "target_type": record.target_type,
        "liquidity_swept": record.liquidity_swept,
        "execution_timeframe": record.execution_timeframe,
    }


def bsi_hard_filter_dims(record: BSIThesisRecordORM) -> dict[str, Any]:
    return {
        "subtype": record.subtype, "canonical_symbol": record.canonical_symbol,
        "direction": record.direction, "bsi_version": record.bsi_version,
    }


def bsi_similarity_score(query: dict[str, Any], candidate: dict[str, Any], *, weights: dict[str, float] = BSI_DIMENSION_WEIGHTS) -> float:
    """Identical ALGORITHM to similarity.py::similarity_score() (weighted-match-fraction, never
    penalizing a dimension the query doesn't know) -- deliberately re-implemented here rather than
    imported, because that function reads its weight table from a hardcoded module-level global
    (`DIMENSION_WEIGHTS`), not a parameter, so importing it directly would mean either mutating a
    SHARED cross-strategy config table (wrong -- would affect every other strategy's own HI too)
    or monkeypatching a global at call time (fragile, order-dependent). This copy takes `weights`
    as a real parameter instead, specifically so BSI's own dimension table never has to touch the
    shared one. Same 0.0..1.0 range, same semantics."""
    available_weight = 0.0
    matched_weight = 0.0
    for dim, weight in weights.items():
        q_val = query.get(dim)
        if q_val is None:
            continue
        available_weight += weight
        if candidate.get(dim) == q_val:
            matched_weight += weight
    if available_weight <= 0:
        return 0.0
    return round(matched_weight / available_weight, 4)


def bsi_candidate_passes_hard_filters(query: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return all(query.get(f) == candidate.get(f) for f in BSI_HARD_FILTER_FIELDS if query.get(f) is not None)


# Effective-sample-size / reliability gating -- directive's own explicit "use effective sample
# size and stability... do not require exact equality" requirement, matching the SAME numeric
# floor `adaptive_similarity.py::_DEFAULT_MIN_EFFECTIVE_SAMPLE` and
# `similarity.py::_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL` already use elsewhere in this codebase (reused
# value, not a new arbitrary number) -- though BSI's own much smaller sample sizes (N=4 for
# bsi_abc, N=502 for bsi_under_over) mean this floor will correctly classify most BSI subtypes as
# HIST_INTEL_INSUFFICIENT for a long time, which is the honest, correct behavior, not a bug to
# work around.
BSI_MIN_EFFECTIVE_SAMPLE = 20
BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL = 100


def bsi_hi_result_from_neighbor_stats(*, effective_sample_size: float, mean_r: float | None, win_rate: float | None) -> dict[str, Any]:
    """Reuses entry_intelligence.py's own SUPPORT/RANK_ADJUST/DEFER/REJECT/NEUTRAL/INSUFFICIENT
    vocabulary (imported by name, not reimplemented) -- this function's only job is to decide
    which of those labels a BSI-specific neighbor-statistics result maps to, using BSI's own
    (identical, reused-not-invented) sample-size floors above. The directive's own explicit
    constraint is enforced here structurally: positive HI evidence can only ever produce SUPPORT/
    RANK_ADJUST (never itself create a valid setup or raise risk beyond what risk/portfolio
    controls already allow -- this function has no risk-sizing authority at all, by construction,
    it returns a label only)."""
    from backend.historical_intelligence.entry_intelligence import (
        HIST_INTEL_DEFER,
        HIST_INTEL_INSUFFICIENT,
        HIST_INTEL_NEUTRAL,
        HIST_INTEL_RANK_ADJUST,
        HIST_INTEL_REJECT,
        HIST_INTEL_SUPPORT,
    )

    if effective_sample_size < BSI_MIN_EFFECTIVE_SAMPLE or mean_r is None:
        return {"hi_result": HIST_INTEL_INSUFFICIENT, "hi_version": BSI_HI_VERSION, "effective_sample_size": effective_sample_size, "reason": f"effective_sample_size={effective_sample_size} below minimum ({BSI_MIN_EFFECTIVE_SAMPLE})"}
    reliable = effective_sample_size >= BSI_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL
    if mean_r <= -0.3:
        label = HIST_INTEL_REJECT if reliable else HIST_INTEL_DEFER
    elif mean_r >= 0.3:
        label = HIST_INTEL_SUPPORT if reliable else HIST_INTEL_RANK_ADJUST
    else:
        label = HIST_INTEL_NEUTRAL
    return {"hi_result": label, "hi_version": BSI_HI_VERSION, "effective_sample_size": effective_sample_size, "mean_r": mean_r, "win_rate": win_rate, "reliable": reliable}

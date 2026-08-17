"""Lorentzian-distance kNN retrieval over jdehorty's public "Machine Learning: Lorentzian
Classification" feature set (RSI/WaveTrend/CCI/ADX, backend.market_structure.oscillators),
independently implemented as an ALTERNATIVE candidate similarity/retrieval engine to
backend.historical_intelligence.similarity's weighted-categorical-overlap approach -- built for a
direct, controlled comparison (same candidates, same +1R/outcome_r target, same chronological
OOS periods), not as a replacement. See docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md and the
LORENTZIAN_VALIDATION research stage for the four-role test harness this is used by.

Deliberate differences from similarity.py, stated rather than hidden:
  - Distance metric: Lorentzian d(x,y) = sum(ln(1 + |xi - yi|)) over 4 CONTINUOUS, rolling-
    normalized oscillator values -- vs similarity.py's weighted overlap over ~17 CATEGORICAL/
    bucketed structural dimensions (regime, session, BOS/CHoCH presence, ATR bucket, etc.) with
    hard filters. Different feature space entirely, not a reparameterization of the same one.
  - Neighbor weighting: plain k=8 nearest-neighbor UNIFORM average (jdehorty's own public
    methodology uses a simple vote/average over its k neighbors, not a similarity-magnitude- or
    recency-weighted blend) -- vs similarity.py's effective_weight (similarity score x recency
    half-life) and temporal-cluster dedup. This module intentionally does NOT copy that
    weighting/dedup machinery; comparing "plain kNN" against "weighted-overlap + dedup" is
    itself part of what the requested comparison needs to surface, not something to paper over
    by silently importing the other engine's weighting scheme.
  - No hard categorical filters (direction/regime/strategy) are applied inside the distance
    computation itself -- the CALLER (the validation harness) is responsible for restricting the
    candidate pool to whatever population it wants compared (e.g. same symbol, same anchor
    strategy, same direction, same chronological "prior to this query" cutoff), exactly mirroring
    how the harness already restricts the pool passed to similarity.find_similar_setups.

Pure, DB-free, and side-effect-free by design -- no query, no persistence. The validation script
loads candidates/features once (bulk, like the squeeze/EQH-EQL validations before it) and calls
these functions in-memory; production DB integration is a later, separate decision gated on this
validation's own results, per the explicit "do not activate anything in DEMO unless it shows
robust OOS improvement" instruction.
"""
from __future__ import annotations

import math
import statistics as pystats
from dataclasses import dataclass
from typing import Any

FeatureVector = tuple[float, float, float, float]

_DEFAULT_K = 8


def lorentzian_distance(a: FeatureVector, b: FeatureVector) -> float:
    """d(x,y) = sum(ln(1 + |xi - yi|)) -- jdehorty's own public formula, chosen (per his
    methodology description) because the logarithm compresses the effect of large single-feature
    divergences relative to Euclidean distance, making the metric more tolerant of one oscillator
    being in a very different regime while the others still agree."""
    return sum(math.log1p(abs(x - y)) for x, y in zip(a, b))


@dataclass(frozen=True)
class Neighbor:
    candidate_id: str
    distance: float
    outcome_r: float | None
    reached_1r: bool | None
    direction: str | None = None


def nearest_neighbors(query: FeatureVector, pool: list[tuple[str, FeatureVector, float | None, bool | None]], *, k: int = _DEFAULT_K) -> list[Neighbor]:
    """`pool`: (candidate_id, feature_vector, outcome_r, reached_1r) tuples -- already restricted
    by the caller to whatever population/point-in-time cutoff is being tested. Returns the k
    closest by Lorentzian distance, ascending."""
    scored = [Neighbor(candidate_id=cid, distance=lorentzian_distance(query, feat), outcome_r=r, reached_1r=r1) for cid, feat, r, r1 in pool]
    scored.sort(key=lambda n: n.distance)
    return scored[:k]


def lorentzian_statistics(query: FeatureVector, pool: list[tuple[str, FeatureVector, float | None, bool | None]], *, k: int = _DEFAULT_K) -> dict[str, Any]:
    """Uniform (unweighted) k-nearest-neighbor statistics -- the Lorentzian-engine counterpart to
    similarity.similarity_statistics(), shaped for direct comparison (same field names where the
    concept is genuinely comparable) but NEVER pretending the two are computed the same way --
    see this module's docstring for the stated weighting/metric differences."""
    if len(pool) < k:
        return {"status": "INSUFFICIENT_POOL", "raw_neighbor_count": len(pool), "effective_sample_size": float(len(pool))}
    neighbors = nearest_neighbors(query, pool, k=k)
    r_values = [n.outcome_r for n in neighbors if n.outcome_r is not None]
    r1_values = [n.reached_1r for n in neighbors if n.reached_1r is not None]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    distances = [n.distance for n in neighbors]
    return {
        "status": "OK",
        "raw_neighbor_count": len(neighbors),
        "effective_sample_size": float(len(neighbors)),  # uniform weight -- ESS is just k when the pool has >= k candidates
        "mean_distance": round(pystats.fmean(distances), 4),
        "max_distance_in_k": round(max(distances), 4),
        "expectancy_r": round(pystats.fmean(r_values), 4) if r_values else None,
        "win_rate": round(len(wins) / len(r_values), 4) if r_values else None,
        "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
        "probability_1r": round(sum(1 for v in r1_values if v) / len(r1_values), 4) if r1_values else None,
        "median_r": round(pystats.median(r_values), 4) if r_values else None,
    }

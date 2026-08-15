"""Multi-neighbor historical analog intelligence (Workstream 8/9, extended for multi-neighbor
analog retrieval).

Complements, never replaces, statistics.py's exact peer_group_hash aggregation. peer_group_hash
(fp-v2) matches on a small HARD set (symbol/direction/strategy/strategy_version/regime_broad/
atr_regime/stop_distance_atr_bucket) -- this module searches a WIDER candidate pool (hard-filtered
on symbol/direction/anchor_strategy/strategy_version/regime_broad -- see REGIME AWARENESS below)
and ranks candidates within it by a transparent, deterministic WEIGHTED similarity score over the
remaining dimensions (session, HTF trend labels, SMC presence booleans, FVG/order-block state,
ATR regime, volatility regime, confidence band, stop-distance/ATR bucket, planned RR bucket).

No black-box ML (explicit requirement) -- every similarity score is `sum(weight_i for dimensions
that match) / sum(weight_i for dimensions the QUERY actually has a value for)`, a single,
auditable formula. Weights are a fixed, documented policy table (DIMENSION_WEIGHTS), not learned.

MANY neighbors, not one pattern (this module's whole reason to exist): find_similar_setups
returns a RANKED SET of historical analogs (configurable top_k), each carrying its own similarity
score/weight -- never a single "closest match." similarity_statistics then computes similarity-
WEIGHTED aggregate outcomes across that set, never a flat average and never a raw count.

Effective sample size: NOT a way to lower the reliability bar. A neighbor with similarity 0.7
contributes 0.7 "effective" samples -- strictly less than a raw count would, never more. Reaching
the same USEFUL/STRONG bar via similarity-weighted evidence therefore requires MORE raw neighbors
than an exact match would, not fewer. Only neighbors at or above _MIN_SIMILARITY_THRESHOLD are
counted at all -- a near-miss on every dimension does not slowly accumulate into a "similar
enough" pattern through volume alone.

REGIME AWARENESS: regime_broad (TRENDING/BREAKOUT/REVERSAL/RANGING/UNKNOWN, the SAME coarse
bucket peer_group_hash already hard-matches on) is now a HARD filter here too -- a range
mean-reversion setup is never blended with a trend-breakout setup just because symbol/strategy
match. When zero neighbors clear both the hard filter and min_similarity, find_similar_setups
returns an empty list and similarity_statistics reports NO_GOOD_HISTORICAL_ANALOG rather than
forcing a weaker match.

TEMPORAL CLUSTERING: neighbors from the same contiguous real market move are NOT independent
evidence, however many fingerprints they produced. cluster_and_dedup groups neighbors by
(canonical_symbol, direction, anchor_strategy, entry_time bucketed into _CLUSTER_WINDOW-wide
windows) and keeps only the highest-similarity representative per cluster -- a real, conservative
fix (it can only REDUCE effective_sample_size relative to the naive sum, never inflate it) for
the documented real risk of one multi-day trend producing dozens of near-identical fingerprints
that would otherwise masquerade as dozens of independent confirmations.
"""
from __future__ import annotations

import math
import statistics as pystats
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.historical_intelligence.statistics import reliability_label
from backend.shared.db import SessionLocal

# Fixed, documented weighting policy -- never learned/opaque. Larger weight = a mismatch on this
# dimension costs more similarity. HARD/near-hard dimensions (symbol, direction, anchor_strategy,
# strategy_version, regime_broad) are enforced as filters in find_similar_setups, not scored here
# -- everything below is the HIGH/MEDIUM/CONTEXTUAL tier scored on a 0..1 weighted basis.
DIMENSION_WEIGHTS: dict[str, float] = {
    # HIGH-WEIGHT
    "regime": 3.0,  # fine-grained regime label, on top of the regime_broad hard filter
    "session": 1.5,
    "h1_trend": 1.5,
    "h4_trend": 1.0,
    "bos_present": 1.2,
    "choch_present": 1.2,
    "mss_present": 1.2,
    "liquidity_sweep_present": 1.2,
    # MEDIUM-WEIGHT
    "atr_regime": 2.0,
    "stop_distance_atr_bucket": 2.0,
    "planned_rr_bucket": 1.5,
    "volatility_regime": 1.0,
    "confidence_band": 1.0,
    "displacement_present": 1.0,
    "fvg_state": 1.0,
    "order_block_state": 1.0,
    # CONTEXTUAL
    "spread_regime": 0.5,
}

# Bumped whenever DIMENSION_WEIGHTS, the hard-filter set, or the ranking/dedup algorithm changes
# -- Redis cache keys embed this (see cache.py::similarity_stats_key) so a model change can never
# serve a stale result computed under the old weighting/filter logic.
SIMILARITY_MODEL_VERSION = "sim-v2"

_MIN_SIMILARITY_THRESHOLD = 0.6
_VERY_CLOSE_THRESHOLD = 0.85  # "exact/very-close" bar for the raw_neighbors vs very_close_matches split
_MIN_EFFECTIVE_SAMPLE_FOR_USEFUL = 100  # SAME numeric bar as statistics.reliability_label's
# USEFUL threshold -- deliberately not lowered (Workstream 13). See module docstring.
_CLUSTER_WINDOW = timedelta(hours=6)  # neighbors from the same symbol+strategy+direction within
# this window of each other are treated as ONE correlated market move, not independent evidence.
_DEFAULT_TOP_K = 100

NO_GOOD_HISTORICAL_ANALOG = "NO_GOOD_HISTORICAL_ANALOG"


def similarity_score(query: dict[str, Any], candidate: dict[str, Any]) -> float:
    """Pure function, no DB access. Both `query` and `candidate` are fingerprint field dicts
    (or subsets) keyed by the DIMENSION_WEIGHTS dimension names. Returns 0.0..1.0."""
    available_weight = 0.0
    matched_weight = 0.0
    for dim, weight in DIMENSION_WEIGHTS.items():
        q_val = query.get(dim)
        if q_val is None:
            continue  # the query doesn't know this dimension -- never penalize for that
        available_weight += weight
        if candidate.get(dim) == q_val:
            matched_weight += weight
    if available_weight <= 0:
        return 0.0
    return round(matched_weight / available_weight, 4)


def _fingerprint_to_dims(fp: HistoricalPatternFingerprintORM) -> dict[str, Any]:
    return {
        "regime": fp.regime, "atr_regime": fp.atr_regime, "stop_distance_atr_bucket": fp.stop_distance_atr_bucket,
        "planned_rr_bucket": fp.planned_rr_bucket, "session": fp.session, "h1_trend": fp.h1_trend, "h4_trend": fp.h4_trend,
        "volatility_regime": fp.volatility_regime, "confidence_band": fp.confidence_band, "bos_present": fp.bos_present,
        "choch_present": fp.choch_present, "mss_present": fp.mss_present, "displacement_present": fp.displacement_present,
        "liquidity_sweep_present": fp.liquidity_sweep_present, "fvg_state": fp.fvg_state, "order_block_state": fp.order_block_state,
        "spread_regime": fp.spread_regime,
    }


def _recency_weight(entry_time: datetime | None, *, now: datetime | None = None, half_life_days: float | None = None) -> float:
    """Optional mild recency multiplier -- OFF by default (half_life_days=None -> 1.0 always).
    When enabled, an exponential decay with the given half-life; never allowed to exceed 1.0 (a
    recent neighbor is never worth MORE than a perfect-similarity match, only less stale)."""
    if half_life_days is None or entry_time is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    entry_time = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - entry_time).total_seconds() / 86400.0)
    return round(0.5 ** (age_days / half_life_days), 6)


def cluster_and_dedup(neighbors: list[dict[str, Any]], *, window: timedelta = _CLUSTER_WINDOW) -> list[dict[str, Any]]:
    """Groups neighbors by (canonical_symbol, direction, anchor_strategy) and, within each group,
    buckets entry_time into `window`-wide sequential clusters (a neighbor starts a new cluster
    once it's more than `window` after the cluster's start) -- keeps only the single
    highest-similarity representative per cluster. Order-preserving is not required; output is
    re-sorted by similarity desc same as the input contract."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for n in neighbors:
        fp = n["fingerprint"]
        key = (fp.canonical_symbol, fp.direction, fp.anchor_strategy)
        groups.setdefault(key, []).append(n)

    kept: list[dict[str, Any]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda n: n["fingerprint"].entry_time)
        cluster: list[dict[str, Any]] = []
        cluster_start = None
        for n in ordered:
            t = n["fingerprint"].entry_time
            if cluster_start is None or (t - cluster_start) > window:
                if cluster:
                    kept.append(max(cluster, key=lambda x: x["similarity"]))
                cluster = [n]
                cluster_start = t
            else:
                cluster.append(n)
        if cluster:
            kept.append(max(cluster, key=lambda x: x["similarity"]))

    kept.sort(key=lambda n: -n["similarity"])
    return kept


def find_similar_setups(
    *, canonical_symbol: str, direction: str, anchor_strategy: str, strategy_version: str,
    query_dims: dict[str, Any], regime_broad: str | None = None, min_similarity: float = _MIN_SIMILARITY_THRESHOLD,
    top_k: int = _DEFAULT_TOP_K, dedup_clusters: bool = True, half_life_days: float | None = None,
    allow_related_symbols: bool = False, related_symbol_penalty: float = 0.5, as_of: datetime | None = None,
    providers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Hard-filters to (symbol, direction, anchor_strategy, strategy_version, regime_broad --
    REGIME AWARENESS, see module docstring), ranks by similarity_score against `query_dims`,
    optionally applies temporal-cluster dedup (default on) and a recency multiplier (default
    off), then returns the top_k. Returns [] when nothing clears the bar -- callers should treat
    an empty list as NO_GOOD_HISTORICAL_ANALOG, never as "expand the search."

    allow_related_symbols (default OFF, not used by any live gate -- see module docstring's
    Symbol Generalization section and the OOS comparison in similarity_oos.py) additionally pulls
    in same-strategy/direction/regime_broad setups on OTHER symbols, each similarity-scored
    normally and then multiplied by `related_symbol_penalty` (<1.0, so a related-symbol neighbor
    can never outweigh a same-symbol one of equal underlying similarity).

    as_of (point-in-time cutoff, used by similarity_oos.py's walk-forward calibration -- never
    passed by the live entry path): restricts the neighbor pool to fingerprints with
    entry_time <= as_of, so an OOS-period query can never see future-relative-to-it evidence.

    providers (default None = no filter, EVERY live/production caller): restricts the neighbor
    pool to fingerprints from the given provider set, e.g. {"MT5"} -- added specifically for
    offline analysis (ForexSB integration directive, Part 9's "does MT5+ForexSB outperform
    MT5-only" comparison), so the same real candidates can be evaluated against a narrower
    corpus WITHOUT duplicating this query/ranking logic in a separate script. Never used by
    entry_intelligence.py or any live gate -- passing it is an explicit, deliberate choice by an
    offline caller only."""
    with SessionLocal() as db:
        query = db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM).join(
            HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id
        ).filter(
            HistoricalPatternFingerprintORM.direction == direction.upper(),
            HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy,
            HistoricalPatternFingerprintORM.strategy_version == strategy_version,
            HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
            HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
        )
        if regime_broad:
            query = query.filter(HistoricalPatternFingerprintORM.regime_broad == regime_broad)
        if as_of is not None:
            query = query.filter(HistoricalPatternFingerprintORM.entry_time <= as_of)
        if providers:
            query = query.filter(HistoricalPatternFingerprintORM.provider.in_({p.upper() for p in providers}))
        if allow_related_symbols:
            rows = query.limit(5000).all()
        else:
            rows = query.filter(HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol.upper()).limit(5000).all()

    neighbors = []
    for fp, outcome in rows:
        score = similarity_score(query_dims, _fingerprint_to_dims(fp))
        if allow_related_symbols and fp.canonical_symbol.upper() != canonical_symbol.upper():
            score = round(score * related_symbol_penalty, 4)
        if score < min_similarity:
            continue
        recency = _recency_weight(fp.entry_time, now=as_of, half_life_days=half_life_days)
        neighbors.append({"fingerprint": fp, "outcome": outcome, "similarity": score, "recency_weight": recency, "effective_weight": round(score * recency, 4)})

    if dedup_clusters:
        neighbors = cluster_and_dedup(neighbors)
    neighbors.sort(key=lambda n: -n["similarity"])
    return neighbors[:top_k]


def _weighted_probability(neighbors: list[dict[str, Any]], field: str, *, weight_key: str = "effective_weight") -> float | None:
    weighted = [(n[weight_key], getattr(n["outcome"], field)) for n in neighbors if getattr(n["outcome"], field) is not None]
    total_weight = sum(w for w, _ in weighted)
    if total_weight <= 0:
        return None
    return round(sum(w * (1.0 if v else 0.0) for w, v in weighted) / total_weight, 4)


def similarity_statistics(
    *, canonical_symbol: str, direction: str, anchor_strategy: str, strategy_version: str, query_dims: dict[str, Any],
    regime_broad: str | None = None, top_k: int = _DEFAULT_TOP_K, half_life_days: float | None = None,
    allow_related_symbols: bool = False, min_similarity: float = _MIN_SIMILARITY_THRESHOLD, as_of: datetime | None = None,
    providers: set[str] | None = None,
) -> dict[str, Any]:
    """The similarity-weighted counterpart to statistics.pattern_statistics -- 'effective_
    sample_size' is the sum of similarity (x recency, if enabled) weights AFTER temporal-cluster
    dedup, never a raw neighbor count. Reports the exact/very-close vs broader-neighbor split,
    weight distribution, and every weighted outcome metric the multi-neighbor model needs.

    providers: see find_similar_setups -- offline-analysis-only corpus restriction, never used
    by a live gate."""
    neighbors = find_similar_setups(
        canonical_symbol=canonical_symbol, direction=direction, anchor_strategy=anchor_strategy,
        strategy_version=strategy_version, query_dims=query_dims, regime_broad=regime_broad,
        top_k=top_k, half_life_days=half_life_days, allow_related_symbols=allow_related_symbols,
        min_similarity=min_similarity, as_of=as_of, providers=providers,
    )
    if not neighbors:
        return {
            "status": NO_GOOD_HISTORICAL_ANALOG, "raw_neighbor_count": 0, "very_close_matches": 0,
            "effective_sample_size": 0.0, "reliability": reliability_label(0),
        }

    weights = [n["effective_weight"] for n in neighbors]
    similarities = [n["similarity"] for n in neighbors]
    effective_sample_size = round(sum(weights), 2)
    very_close_matches = sum(1 for s in similarities if s >= _VERY_CLOSE_THRESHOLD)

    r_pairs = [(n["effective_weight"], n["outcome"].outcome_r) for n in neighbors if n["outcome"].outcome_r is not None]
    net_r_pairs = [(n["effective_weight"], n["outcome"].net_outcome_r) for n in neighbors if n["outcome"].net_outcome_r is not None]
    total_r_weight = sum(w for w, _ in r_pairs)
    total_net_r_weight = sum(w for w, _ in net_r_pairs)
    weighted_expectancy = round(sum(w * r for w, r in r_pairs) / total_r_weight, 4) if total_r_weight > 0 else None
    weighted_net_expectancy = round(sum(w * r for w, r in net_r_pairs) / total_net_r_weight, 4) if total_net_r_weight > 0 else None

    win_pairs = [(w, r) for w, r in r_pairs if r > 0]
    loss_pairs = [(w, r) for w, r in r_pairs if r < 0]
    weighted_gross_win = sum(w * r for w, r in win_pairs)
    weighted_gross_loss = abs(sum(w * r for w, r in loss_pairs))
    weighted_win_rate = round(sum(w for w, _ in win_pairs) / total_r_weight, 4) if total_r_weight > 0 else None

    r_values = [r for _w, r in r_pairs]
    mfe_pairs = [(n["effective_weight"], n["outcome"].mfe_r) for n in neighbors if n["outcome"].mfe_r is not None]
    mae_pairs = [(n["effective_weight"], n["outcome"].mae_r) for n in neighbors if n["outcome"].mae_r is not None]
    mfe_weight = sum(w for w, _ in mfe_pairs)
    mae_weight = sum(w for w, _ in mae_pairs)

    return {
        "status": "OK",
        "raw_neighbor_count": len(neighbors),
        "very_close_matches": very_close_matches,
        "effective_sample_size": effective_sample_size,
        "reliability": reliability_label(int(effective_sample_size)),
        "similarity_weight_distribution": {
            "max": round(max(similarities), 4), "median": round(pystats.median(similarities), 4),
            "min": round(min(similarities), 4), "mean": round(pystats.fmean(similarities), 4),
        },
        "median_similarity": round(pystats.median(similarities), 4),
        "top_match_quality": round(max(similarities), 4),
        "minimum_accepted_similarity": _MIN_SIMILARITY_THRESHOLD,
        "weighted_expectancy_r": weighted_expectancy,
        "weighted_net_expectancy_r": weighted_net_expectancy,
        "weighted_win_rate": weighted_win_rate,
        "weighted_profit_factor": round(weighted_gross_win / weighted_gross_loss, 4) if weighted_gross_loss > 0 else None,
        "weighted_median_r": round(pystats.median(r_values), 4) if r_values else None,
        "weighted_immediate_failure_probability": _weighted_probability(neighbors, "immediate_failure"),
        "weighted_probability_0_25r": _weighted_probability(neighbors, "reached_0_25r"),
        "weighted_probability_0_5r": _weighted_probability(neighbors, "reached_0_5r"),
        "weighted_probability_0_75r": _weighted_probability(neighbors, "reached_0_75r"),
        "weighted_probability_1r": _weighted_probability(neighbors, "reached_1r"),
        "weighted_probability_1_5r": _weighted_probability(neighbors, "reached_1_5r"),
        "weighted_probability_2r": _weighted_probability(neighbors, "reached_2r"),
        "weighted_probability_tp": _weighted_probability(neighbors, "tp_hit"),
        "weighted_probability_sl": _weighted_probability(neighbors, "sl_hit"),
        "weighted_mfe_r": round(sum(w * v for w, v in mfe_pairs) / mfe_weight, 4) if mfe_weight > 0 else None,
        "weighted_mae_r": round(sum(w * v for w, v in mae_pairs) / mae_weight, 4) if mae_weight > 0 else None,
    }

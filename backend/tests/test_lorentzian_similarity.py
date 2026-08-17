"""Tests for backend/historical_intelligence/lorentzian_similarity.py -- the Lorentzian-distance
kNN retrieval engine (jdehorty methodology, see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md).
Pure-function tests only; not wired into any live decision yet."""
from __future__ import annotations

from backend.historical_intelligence.lorentzian_similarity import (
    lorentzian_distance, lorentzian_statistics, nearest_neighbors,
)


def test_lorentzian_distance_identical_vectors_is_zero():
    v = (0.5, 0.5, 0.5, 0.5)
    assert lorentzian_distance(v, v) == 0.0


def test_lorentzian_distance_is_symmetric():
    a, b = (0.1, 0.9, 0.3, 0.7), (0.8, 0.2, 0.6, 0.1)
    assert abs(lorentzian_distance(a, b) - lorentzian_distance(b, a)) < 1e-12


def test_lorentzian_distance_grows_with_divergence():
    query = (0.5, 0.5, 0.5, 0.5)
    close = (0.51, 0.49, 0.5, 0.52)
    far = (0.99, 0.01, 0.95, 0.02)
    assert lorentzian_distance(query, close) < lorentzian_distance(query, far)


def test_lorentzian_distance_compresses_large_divergence_vs_euclidean_scaling():
    """The whole point of ln(1+|dx|) over raw |dx|: a single feature's large divergence should
    NOT dominate the total distance the way squared-Euclidean would."""
    query = (0.0, 0.0, 0.0, 0.0)
    one_feature_maxed = (1.0, 0.0, 0.0, 0.0)
    all_features_moderate = (0.3, 0.3, 0.3, 0.3)
    d_one_maxed = lorentzian_distance(query, one_feature_maxed)
    d_all_moderate = lorentzian_distance(query, all_features_moderate)
    # ln(1+1)=0.693 vs 4*ln(1.3)=1.05 -- the spread-out-moderate vector is FARTHER under
    # Lorentzian distance than the one-feature-extreme vector, unlike squared-Euclidean
    # (1.0 vs 0.36) where the extreme vector would dominate.
    assert d_all_moderate > d_one_maxed


def test_nearest_neighbors_returns_k_closest_sorted_ascending():
    query = (0.5, 0.5, 0.5, 0.5)
    pool = [
        ("far", (0.99, 0.99, 0.01, 0.01), 1.0, True),
        ("near1", (0.51, 0.5, 0.49, 0.5), 0.5, True),
        ("near2", (0.5, 0.52, 0.5, 0.48), -0.2, False),
        ("mid", (0.7, 0.3, 0.6, 0.4), 0.1, False),
    ]
    result = nearest_neighbors(query, pool, k=2)
    assert [n.candidate_id for n in result] == ["near1", "near2"] or [n.candidate_id for n in result] == ["near2", "near1"]
    assert result[0].distance <= result[1].distance


def test_lorentzian_statistics_insufficient_pool():
    query = (0.5, 0.5, 0.5, 0.5)
    pool = [("a", (0.5, 0.5, 0.5, 0.5), 1.0, True)]
    stats = lorentzian_statistics(query, pool, k=8)
    assert stats["status"] == "INSUFFICIENT_POOL"
    assert stats["raw_neighbor_count"] == 1


def test_lorentzian_statistics_uniform_weighted_expectancy():
    query = (0.5, 0.5, 0.5, 0.5)
    # 8 candidates, all equidistant-ish and close to the query -- expectancy should be the
    # plain (unweighted) mean of their outcome_r values.
    pool = [(f"c{i}", (0.5 + i * 0.001, 0.5, 0.5, 0.5), r, r > 0) for i, r in enumerate([1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, 1.0])]
    stats = lorentzian_statistics(query, pool, k=8)
    assert stats["status"] == "OK"
    assert stats["raw_neighbor_count"] == 8
    assert stats["expectancy_r"] == 0.25  # (5*1 - 3*1)/8
    assert stats["win_rate"] == 0.625


def test_lorentzian_statistics_prefers_the_true_nearest_k_when_pool_is_larger():
    query = (0.5, 0.5, 0.5, 0.5)
    near_pool = [(f"near{i}", (0.5, 0.5, 0.5, 0.5), 1.0, True) for i in range(8)]
    far_pool = [(f"far{i}", (0.0, 1.0, 0.0, 1.0), -1.0, False) for i in range(20)]
    stats = lorentzian_statistics(query, near_pool + far_pool, k=8)
    assert stats["expectancy_r"] == 1.0  # only the 8 truly-near candidates should be selected
    assert stats["win_rate"] == 1.0

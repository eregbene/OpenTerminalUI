"""Regression tests for backend/historical_intelligence/adaptive_cache.py (Adaptive-Historical-
Intelligence-Backfill directive, Phase 21/22): sync-Redis fast path in front of the exact
peer-group and weighted multi-neighbor state statistics, used by evaluate_adaptive_intelligence
from inside asyncio.to_thread. Fail-open on any Redis error, versioned keys, cache-hit skips
Postgres entirely.
"""
from __future__ import annotations

import time

from backend.historical_intelligence import adaptive_cache


class _FakeSyncRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def ping(self):
        return True

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value


class _AlwaysBrokenRedis:
    def ping(self):
        raise ConnectionError("simulated outage")

    def get(self, key):
        raise ConnectionError("simulated outage")

    def set(self, key, value, ex=None):
        raise ConnectionError("simulated outage")


def _reset_metrics():
    with adaptive_cache._metrics_lock:
        for k in adaptive_cache._metrics:
            adaptive_cache._metrics[k] = 0


def test_weighted_state_statistics_cache_miss_then_hit(monkeypatch):
    fake = _FakeSyncRedis()
    monkeypatch.setattr(adaptive_cache, "_get_sync_redis", lambda: fake)
    _reset_metrics()

    calls = {"n": 0}

    def _fake_weighted(*, strategy, symbol, direction, query_fields, top_k):
        calls["n"] += 1
        return {"status": "OK", "effective_sample_size": 42.0, "reliability": "USEFUL"}

    import backend.historical_intelligence.adaptive_similarity as real_mod
    monkeypatch.setattr(real_mod, "weighted_state_statistics", _fake_weighted)

    query_fields = {"current_r_bucket": "R_0_50", "mfe_bucket": "MODERATE"}
    r1 = adaptive_cache.cached_weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=query_fields)
    assert r1["_cache_source"] == "postgres"
    assert r1["status"] == "OK"
    assert calls["n"] == 1

    r2 = adaptive_cache.cached_weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=query_fields)
    assert r2["_cache_source"] == "redis"
    assert r2["status"] == "OK"
    assert calls["n"] == 1  # Postgres not touched again -- served entirely from the fake Redis store

    snap = adaptive_cache.metrics_snapshot()
    assert snap["cache_hits"] == 1
    assert snap["cache_misses"] == 1


def test_exact_state_statistics_cache_miss_then_hit(monkeypatch):
    fake = _FakeSyncRedis()
    monkeypatch.setattr(adaptive_cache, "_get_sync_redis", lambda: fake)
    _reset_metrics()

    calls = {"n": 0}

    def _fake_exact(peer_group_hash):
        calls["n"] += 1
        return {"peer_group_hash": peer_group_hash, "resolved_sample_size": 5, "reliability": "LOW"}

    import backend.historical_intelligence.adaptive_statistics as real_stats_mod
    monkeypatch.setattr(real_stats_mod, "state_statistics", _fake_exact)

    r1 = adaptive_cache.cached_exact_state_statistics("PGH_ABC123")
    assert r1["_cache_source"] == "postgres"
    assert calls["n"] == 1

    r2 = adaptive_cache.cached_exact_state_statistics("PGH_ABC123")
    assert r2["_cache_source"] == "redis"
    assert calls["n"] == 1


def test_cache_key_changes_with_query_fields():
    key_a = adaptive_cache.adaptive_stats_key(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields={"current_r_bucket": "R_0_50"}, top_k=50)
    key_b = adaptive_cache.adaptive_stats_key(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields={"current_r_bucket": "R_1_00"}, top_k=50)
    key_c = adaptive_cache.adaptive_stats_key(strategy="mtfai1", symbol="EURUSD", direction="SHORT", query_fields={"current_r_bucket": "R_0_50"}, top_k=50)
    assert key_a != key_b  # different query field -> different cache entry
    assert key_a != key_c  # different direction -> different cache entry


def test_cache_key_carries_all_four_version_components():
    key = adaptive_cache.adaptive_stats_key(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields={}, top_k=50)
    from backend.historical_intelligence.adaptive_similarity import ADAPTIVE_SIMILARITY_MODEL_VERSION
    from backend.historical_intelligence.orm import ADAPTIVE_STATE_MODEL_VERSION, HISTORICAL_INTELLIGENCE_VERSION
    from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION

    assert HISTORICAL_INTELLIGENCE_VERSION in key
    assert STRATEGY_REPLAY_VERSION in key
    assert ADAPTIVE_STATE_MODEL_VERSION in key
    assert ADAPTIVE_SIMILARITY_MODEL_VERSION in key


def test_fail_open_when_redis_get_raises(monkeypatch):
    monkeypatch.setattr(adaptive_cache, "_get_sync_redis", lambda: _AlwaysBrokenRedis())
    _reset_metrics()

    def _fake_weighted(*, strategy, symbol, direction, query_fields, top_k):
        return {"status": "OK", "effective_sample_size": 10.0}

    import backend.historical_intelligence.adaptive_similarity as real_mod
    monkeypatch.setattr(real_mod, "weighted_state_statistics", _fake_weighted)

    result = adaptive_cache.cached_weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields={})
    assert result["_cache_source"] == "postgres"  # degraded straight through, no exception raised
    assert result["status"] == "OK"


def test_fail_open_when_redis_client_unavailable(monkeypatch):
    monkeypatch.setattr(adaptive_cache, "_get_sync_redis", lambda: None)
    _reset_metrics()

    def _fake_exact(peer_group_hash):
        return {"peer_group_hash": peer_group_hash, "resolved_sample_size": 0}

    import backend.historical_intelligence.adaptive_statistics as real_stats_mod
    monkeypatch.setattr(real_stats_mod, "state_statistics", _fake_exact)

    result = adaptive_cache.cached_exact_state_statistics("PGH_XYZ")
    assert result["_cache_source"] == "postgres"


def test_metrics_snapshot_hit_rate_and_latency():
    _reset_metrics()
    with adaptive_cache._metrics_lock:
        adaptive_cache._metrics["cache_hits"] = 3
        adaptive_cache._metrics["cache_misses"] = 1
        adaptive_cache._metrics["hit_count"] = 3
        adaptive_cache._metrics["hit_latency_ms_total"] = 1.5

    snap = adaptive_cache.metrics_snapshot()
    assert snap["hit_rate"] == 0.75
    assert snap["avg_hit_latency_ms"] == 0.5


def test_sync_redis_client_lazily_created_once(monkeypatch):
    """Mirrors the same singleton-client guarantee circuit_breaker.py's own test enforces for
    ITS sync client -- adaptive_cache.py must not reconnect to Redis on every call either."""
    created = []

    class _Stub:
        def ping(self):
            return True

    class _FakeSyncRedisModule:
        class Redis:
            @staticmethod
            def from_url(*a, **k):
                created.append(_Stub())
                return created[-1]

    import sys

    monkeypatch.setattr(adaptive_cache, "_sync_redis_client", None)
    monkeypatch.setattr(adaptive_cache, "_sync_redis_unavailable", False)
    monkeypatch.setitem(sys.modules, "redis", _FakeSyncRedisModule)

    first = adaptive_cache._get_sync_redis()
    second = adaptive_cache._get_sync_redis()
    assert first is second
    assert len(created) == 1

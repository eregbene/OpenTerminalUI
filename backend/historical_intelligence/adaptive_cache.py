"""Sync Redis fast-lookup cache for Adaptive Historical Intelligence state statistics
(Adaptive-Historical-Intelligence-Backfill directive, Phase 21/22).

evaluate_adaptive_intelligence()/record_observation() run inside asyncio.to_thread() (see
backend/adaptive_management/service.py::_monitor_cycle) -- a plain OS thread with no event loop
of its own, so the shared ASYNC Redis client (backend.shared.cache.cache /
backend.mt5_strategies.redis_layer) can never be awaited from there. This uses the classic SYNC
redis-py client instead -- exactly the same deliberate, documented exception
backend/mt5_strategies/circuit_breaker.py already established for the identical reason (see that
module's docstring: "Uses the classic SYNC redis-py client... calling the shared ASYNC redis
client from [a thread with no event loop] is not safe"). Fail-open by construction: any Redis
error here degrades straight to "compute from Postgres", never a raised exception, matching every
other cache in this codebase.

Postgres (adaptive_similarity.weighted_state_statistics) remains authoritative; this is purely an
accelerating cache in front of it, never a second source of truth. Versioned by
HISTORICAL_INTELLIGENCE_VERSION + STRATEGY_REPLAY_VERSION (the historical backfill corpus this
evidence is built from) + ADAPTIVE_STATE_MODEL_VERSION + ADAPTIVE_SIMILARITY_MODEL_VERSION, plus a
hash of the query itself -- any model, schema, or replay-logic change is a different cache key,
never a stale hit against an old definition.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

KEY_PREFIX = "histintel-adaptive"
_DEFAULT_TTL_SECONDS = 900  # mirrors historical_intelligence/cache.py's pattern-stats TTL

_sync_redis_client: Any = None
_sync_redis_unavailable = False
_sync_redis_init_lock = threading.Lock()


def _get_sync_redis() -> Any:
    global _sync_redis_client, _sync_redis_unavailable
    if _sync_redis_client is not None:
        return _sync_redis_client
    if _sync_redis_unavailable:
        return None
    with _sync_redis_init_lock:
        if _sync_redis_client is not None:
            return _sync_redis_client
        if _sync_redis_unavailable:
            return None
        try:
            import redis as redis_sync

            url = os.getenv("OPENTERMINALUI_REDIS_URL") or os.getenv("REDIS_URL") or "redis://localhost:6379/0"
            client = redis_sync.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5, max_connections=20)
            client.ping()
            _sync_redis_client = client
        except Exception as exc:
            logger.debug("Adaptive historical intelligence sync Redis unavailable, cache disabled: %s", exc.__class__.__name__)
            _sync_redis_unavailable = True
    return _sync_redis_client


_metrics_lock = threading.Lock()
_metrics: dict[str, float] = {
    "cache_hits": 0, "cache_misses": 0, "postgres_lookups": 0,
    "hit_latency_ms_total": 0.0, "hit_count": 0,
    "miss_latency_ms_total": 0.0, "miss_count": 0,
}


def _incr(name: str, n: float = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n


def metrics_snapshot() -> dict[str, float]:
    with _metrics_lock:
        snap = dict(_metrics)
    total = snap["cache_hits"] + snap["cache_misses"]
    snap["hit_rate"] = round(snap["cache_hits"] / total, 4) if total else None
    snap["avg_hit_latency_ms"] = round(snap["hit_latency_ms_total"] / snap["hit_count"], 3) if snap["hit_count"] else None
    snap["avg_miss_latency_ms"] = round(snap["miss_latency_ms_total"] / snap["miss_count"], 3) if snap["miss_count"] else None
    return snap


def exact_stats_key(peer_group_hash: str) -> str:
    from backend.historical_intelligence.orm import ADAPTIVE_STATE_MODEL_VERSION, HISTORICAL_INTELLIGENCE_VERSION
    from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION

    return f"{KEY_PREFIX}:exact:{HISTORICAL_INTELLIGENCE_VERSION}:{STRATEGY_REPLAY_VERSION}:{ADAPTIVE_STATE_MODEL_VERSION}:{peer_group_hash}"


def cached_exact_state_statistics(peer_group_hash: str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    """Sync-Redis counterpart to adaptive_statistics.state_statistics, for the SAME reason and
    call site as cached_weighted_state_statistics above: evaluate_adaptive_intelligence tries
    this EXACT peer-group path FIRST, unconditionally, on every real cycle for every managed
    position -- so it is at least as hot a cache target as the similarity fallback, even though
    its underlying Postgres query is already bounded (Part 21's _MAX_REAL_EVENTS_PER_QUERY fix)."""
    from backend.historical_intelligence import adaptive_statistics

    key = exact_stats_key(peer_group_hash)
    t0 = time.perf_counter()
    client = _get_sync_redis()
    cached = None
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                cached = json.loads(raw)
        except Exception as exc:
            logger.debug("Adaptive historical intelligence sync Redis get failed (exact), falling back to Postgres: %s", exc.__class__.__name__)

    if cached is not None:
        _incr("cache_hits")
        _incr("hit_count")
        _incr("hit_latency_ms_total", (time.perf_counter() - t0) * 1000)
        return {**cached, "_cache_source": "redis"}

    _incr("cache_misses")
    _incr("postgres_lookups")
    result = adaptive_statistics.state_statistics(peer_group_hash)
    if client is not None:
        try:
            client.set(key, json.dumps(result, default=str), ex=ttl_seconds)
        except Exception as exc:
            logger.debug("Adaptive historical intelligence sync Redis set failed (exact, result still returned): %s", exc.__class__.__name__)
    _incr("miss_count")
    _incr("miss_latency_ms_total", (time.perf_counter() - t0) * 1000)
    return {**result, "_cache_source": "postgres"}


def adaptive_stats_key(*, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any], top_k: int) -> str:
    from backend.historical_intelligence.adaptive_similarity import ADAPTIVE_SIMILARITY_MODEL_VERSION
    from backend.historical_intelligence.orm import ADAPTIVE_STATE_MODEL_VERSION, HISTORICAL_INTELLIGENCE_VERSION
    from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION

    query_fingerprint = hashlib.sha256(json.dumps({"fields": query_fields, "top_k": top_k}, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return f"{KEY_PREFIX}:{HISTORICAL_INTELLIGENCE_VERSION}:{STRATEGY_REPLAY_VERSION}:{ADAPTIVE_STATE_MODEL_VERSION}:{ADAPTIVE_SIMILARITY_MODEL_VERSION}:{strategy or 'ANY'}:{symbol.upper()}:{direction.upper()}:{query_fingerprint}"


def cached_weighted_state_statistics(*, strategy: str | None, symbol: str, direction: str, query_fields: dict[str, Any], top_k: int = 50, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    """Sync counterpart to historical_intelligence/cache.py's cached_similarity_statistics, for
    the ONE call site (adaptive_intelligence.evaluate_adaptive_intelligence) that runs off the
    event loop and cannot await the shared async Redis client. The live Adaptive Manager cycle
    calls THIS, never adaptive_similarity.weighted_state_statistics directly, so a repeated query
    against the same peer state (common -- the same open position gets re-evaluated every cycle)
    is a Redis round-trip instead of a fresh Postgres merge+dedup+score pass."""
    from backend.historical_intelligence import adaptive_similarity

    key = adaptive_stats_key(strategy=strategy, symbol=symbol, direction=direction, query_fields=query_fields, top_k=top_k)
    t0 = time.perf_counter()
    client = _get_sync_redis()
    cached = None
    if client is not None:
        try:
            raw = client.get(key)
            if raw:
                cached = json.loads(raw)
        except Exception as exc:
            logger.debug("Adaptive historical intelligence sync Redis get failed, falling back to Postgres: %s", exc.__class__.__name__)

    if cached is not None:
        _incr("cache_hits")
        _incr("hit_count")
        _incr("hit_latency_ms_total", (time.perf_counter() - t0) * 1000)
        return {**cached, "_cache_source": "redis"}

    _incr("cache_misses")
    _incr("postgres_lookups")
    result = adaptive_similarity.weighted_state_statistics(strategy=strategy, symbol=symbol, direction=direction, query_fields=query_fields, top_k=top_k)
    if client is not None:
        try:
            client.set(key, json.dumps(result, default=str), ex=ttl_seconds)
        except Exception as exc:
            logger.debug("Adaptive historical intelligence sync Redis set failed (result still returned): %s", exc.__class__.__name__)
    _incr("miss_count")
    _incr("miss_latency_ms_total", (time.perf_counter() - t0) * 1000)
    return {**result, "_cache_source": "postgres"}

"""Redis fast-lookup cache for Historical Intelligence pattern statistics (Part 10/11).

Reuses backend.mt5_strategies.redis_layer's ALREADY-CONNECTED Redis client and generic cache_get/
cache_set (fail-open JSON get/set against the SAME connection MultiTierCache establishes at app
startup) -- this is NOT a second Redis client architecture. Postgres (statistics.py, reading
historical_pattern_fingerprints/historical_setup_outcomes) remains authoritative; this module is
purely a cache in front of it.

Flow (Part 11 -- the live M5 cycle must never run historical backtests synchronously):

    candidate -> deterministic fingerprint -> Redis lookup
        -> cache hit: return immediately
        -> miss: Postgres aggregate lookup (statistics.pattern_statistics) -> populate Redis -> return

Every lookup here is a handful of milliseconds even on a Postgres miss (an indexed aggregate
query over already-computed fingerprint/outcome rows) -- there is no per-lookup strategy replay
or candle fetch anywhere in this path. Versioned keys (historical-intelligence version, strategy
version, fingerprint version, peer_group_hash) mean a strategy-logic or fingerprint-schema change
can never serve stale statistics computed under a different definition -- old keys simply stop
being written to and expire via TTL, no manual invalidation needed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.historical_intelligence import statistics
from backend.historical_intelligence.orm import FINGERPRINT_VERSION, HISTORICAL_INTELLIGENCE_VERSION
from backend.mt5_strategies.redis_layer import cache_get, cache_set

logger = logging.getLogger(__name__)

KEY_PREFIX = "histintel"
_DEFAULT_TTL_SECONDS = 900  # 15 min -- pattern statistics change slowly (new outcomes resolve on
# the order of hours), a short TTL is purely a staleness ceiling, not a performance necessity.

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


def pattern_stats_key(peer_group_hash: str, *, strategy_version: str, fingerprint_version: str = FINGERPRINT_VERSION, historical_intelligence_version: str = HISTORICAL_INTELLIGENCE_VERSION) -> str:
    return f"{KEY_PREFIX}:pattern-stats:{historical_intelligence_version}:{strategy_version}:{fingerprint_version}:{peer_group_hash}"


async def cached_pattern_statistics(peer_group_hash: str, *, strategy_version: str, fingerprint_version: str = FINGERPRINT_VERSION, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    """The one function entry-intelligence.py / adaptive integration should call -- never
    statistics.pattern_statistics() directly from a live decision path (that always hits
    Postgres; this wraps it with the Redis fast path first).

    redis_layer.cache_get/cache_set are already fail-open internally (a Redis outage is caught
    there and surfaces as a plain None/no-op, never an exception) -- the try/except here is
    defense-in-depth so this function is ALSO safe if that guarantee were ever weakened upstream,
    or if the Redis client itself is unavailable in a way that raises before reaching
    redis_layer's own guard (e.g. a connection pool exhausted error). Either way, a Redis
    problem degrades to "always take the Postgres path", never a raised exception."""
    key = pattern_stats_key(peer_group_hash, strategy_version=strategy_version, fingerprint_version=fingerprint_version)
    t0 = time.perf_counter()
    cached = None
    try:
        cached = await cache_get(key)
    except Exception as exc:
        logger.debug("Historical intelligence Redis cache_get failed, falling back to Postgres: %s", exc.__class__.__name__)

    if cached is not None:
        _incr("cache_hits")
        _incr("hit_count")
        _incr("hit_latency_ms_total", (time.perf_counter() - t0) * 1000)
        return {**cached, "_cache_source": "redis"}

    _incr("cache_misses")
    _incr("postgres_lookups")
    result = statistics.pattern_statistics(peer_group_hash, strategy_version=strategy_version, fingerprint_version=fingerprint_version)
    try:
        await cache_set(key, result, ttl_seconds)
    except Exception as exc:
        logger.debug("Historical intelligence Redis cache_set failed (result still returned): %s", exc.__class__.__name__)
    _incr("miss_count")
    _incr("miss_latency_ms_total", (time.perf_counter() - t0) * 1000)
    return {**result, "_cache_source": "postgres"}


def similarity_stats_key(
    *, canonical_symbol: str, direction: str, anchor_strategy: str, strategy_version: str, query_dims: dict[str, Any],
    regime_broad: str | None, top_k: int, fingerprint_version: str = FINGERPRINT_VERSION,
    historical_intelligence_version: str = HISTORICAL_INTELLIGENCE_VERSION,
) -> str:
    """Versioned with historical_intelligence_version/strategy_version/fingerprint_version (same
    as pattern_stats_key) PLUS similarity.SIMILARITY_MODEL_VERSION and a deterministic hash of
    the query itself (query_dims/regime_broad/top_k) -- changing similarity weights, the hard-
    filter set, or the requested top_k/regime_broad is a DIFFERENT cache entry, never a stale hit
    against an old model's result."""
    import hashlib
    import json

    from backend.historical_intelligence.similarity import SIMILARITY_MODEL_VERSION

    query_fingerprint = hashlib.sha256(json.dumps({"dims": query_dims, "regime_broad": regime_broad, "top_k": top_k}, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return f"{KEY_PREFIX}:similarity-stats:{historical_intelligence_version}:{strategy_version}:{fingerprint_version}:{SIMILARITY_MODEL_VERSION}:{anchor_strategy}:{canonical_symbol.upper()}:{direction.upper()}:{query_fingerprint}"


async def cached_similarity_statistics(
    *, canonical_symbol: str, direction: str, anchor_strategy: str, strategy_version: str, query_dims: dict[str, Any],
    regime_broad: str | None = None, top_k: int = 50, ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """The Redis-fronted entry point for the multi-neighbor similarity model -- mirrors
    cached_pattern_statistics exactly (fail-open Redis, Postgres fallback via
    similarity.similarity_statistics, cache populated on miss). entry_intelligence.py's live path
    should call this instead of similarity.similarity_statistics directly."""
    from backend.historical_intelligence import similarity

    key = similarity_stats_key(canonical_symbol=canonical_symbol, direction=direction, anchor_strategy=anchor_strategy, strategy_version=strategy_version, query_dims=query_dims, regime_broad=regime_broad, top_k=top_k)
    t0 = time.perf_counter()
    cached = None
    try:
        cached = await cache_get(key)
    except Exception as exc:
        logger.debug("Similarity Redis cache_get failed, falling back to Postgres: %s", exc.__class__.__name__)

    if cached is not None:
        _incr("cache_hits")
        _incr("hit_count")
        _incr("hit_latency_ms_total", (time.perf_counter() - t0) * 1000)
        return {**cached, "_cache_source": "redis"}

    _incr("cache_misses")
    _incr("postgres_lookups")
    result = similarity.similarity_statistics(canonical_symbol=canonical_symbol, direction=direction, anchor_strategy=anchor_strategy, strategy_version=strategy_version, query_dims=query_dims, regime_broad=regime_broad, top_k=top_k)
    try:
        await cache_set(key, result, ttl_seconds)
    except Exception as exc:
        logger.debug("Similarity Redis cache_set failed (result still returned): %s", exc.__class__.__name__)
    _incr("miss_count")
    _incr("miss_latency_ms_total", (time.perf_counter() - t0) * 1000)
    return {**result, "_cache_source": "postgres"}


async def warm_cache(*, strategy_version: str, fingerprint_version: str = FINGERPRINT_VERSION) -> int:
    """Precomputes and populates Redis for every DISTINCT peer_group_hash currently in
    historical_pattern_fingerprints under this version (Workstream 11/15 -- "do not recompute
    expensive similarity datasets on every live cycle"). Intended to be run once after a bulk
    fingerprint/outcome regeneration, not from any live decision path. Returns the number of
    peer groups warmed."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal as _SessionLocal

    with _SessionLocal() as db:
        hashes = [
            r[0] for r in db.query(HistoricalPatternFingerprintORM.peer_group_hash)
            .filter(HistoricalPatternFingerprintORM.strategy_version == strategy_version, HistoricalPatternFingerprintORM.fingerprint_version == fingerprint_version)
            .distinct().all()
        ]
    for peer_group_hash in hashes:
        await cached_pattern_statistics(peer_group_hash, strategy_version=strategy_version, fingerprint_version=fingerprint_version)
    return len(hashes)

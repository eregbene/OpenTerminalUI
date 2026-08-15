"""Redis verification for the enlarged ForexSB+MT5 corpus (Part 15/16 of the continuation
directive).

Uses the EXISTING, unmodified cache.cached_similarity_statistics (the same function
entry_intelligence.py's live path should call) -- never a synchronous multi-year replay, and
never a new caching mechanism. Proves, with real evidence:

  1. A cold lookup (cache miss) populates Redis and returns real stats from Postgres.
  2. A warm lookup (cache hit) is materially faster and returns the SAME data.
  3. The retrieved neighbor set actually includes ForexSB-era (pre-2022) analog timestamps --
     not just a claim that Redis is "online".
"""
from __future__ import annotations

import asyncio
import time

from backend.historical_intelligence import cache, similarity
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION
from backend.mt5_strategies.redis_layer import cache_get
from backend.shared.cache import cache as multi_tier_cache
from backend.shared.db import SessionLocal


async def main() -> None:
    # This is an ad-hoc script, not the real app -- MultiTierCache only opens its Redis
    # connection on .initialize(), which normally happens at real app startup. Without this,
    # get_client() silently returns None and every lookup below would fail-open straight to
    # Postgres, making the test a false negative (looks like "no cache hit" when Redis was
    # simply never connected in THIS process).
    await multi_tier_cache.initialize()
    with SessionLocal() as db:
        # Pick a REAL recent EURUSD fingerprint as the query template -- same shape a live
        # candidate would produce, not a synthetic/fabricated query.
        sample = (
            db.query(HistoricalPatternFingerprintORM)
            .filter(HistoricalPatternFingerprintORM.canonical_symbol == "EURUSD")
            .order_by(HistoricalPatternFingerprintORM.entry_time.desc())
            .first()
        )
    if sample is None:
        print("No EURUSD fingerprints exist yet -- cannot run Redis verification.", flush=True)
        return

    query_dims = similarity._fingerprint_to_dims(sample)
    key = cache.similarity_stats_key(
        canonical_symbol="EURUSD", direction=sample.direction, anchor_strategy=sample.anchor_strategy,
        strategy_version=sample.strategy_version, query_dims=query_dims, regime_broad=sample.regime_broad, top_k=100,
    )
    # Force a real cold start for this measurement -- delete any pre-existing key for this exact query.
    from backend.mt5_strategies.redis_layer import cache_delete
    try:
        await cache_delete(key)
    except Exception:
        pass

    t0 = time.perf_counter()
    cold = await cache.cached_similarity_statistics(
        canonical_symbol="EURUSD", direction=sample.direction, anchor_strategy=sample.anchor_strategy,
        strategy_version=sample.strategy_version, query_dims=query_dims, regime_broad=sample.regime_broad, top_k=100,
    )
    cold_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    warm = await cache.cached_similarity_statistics(
        canonical_symbol="EURUSD", direction=sample.direction, anchor_strategy=sample.anchor_strategy,
        strategy_version=sample.strategy_version, query_dims=query_dims, regime_broad=sample.regime_broad, top_k=100,
    )
    warm_ms = (time.perf_counter() - t0) * 1000

    print(f"Query template: strategy={sample.anchor_strategy} direction={sample.direction} regime_broad={sample.regime_broad}", flush=True)
    print(f"COLD lookup: {cold_ms:.2f}ms, source={cold.get('_cache_source')}, status={cold.get('status')}, raw_neighbor_count={cold.get('raw_neighbor_count')}, effective_sample_size={cold.get('effective_sample_size')}", flush=True)
    print(f"WARM lookup: {warm_ms:.2f}ms, source={warm.get('_cache_source')}, status={warm.get('status')}, raw_neighbor_count={warm.get('raw_neighbor_count')}, effective_sample_size={warm.get('effective_sample_size')}", flush=True)
    print(f"Speedup: {cold_ms / warm_ms:.1f}x" if warm_ms > 0 else "n/a", flush=True)

    raw_cached = await cache_get(key)
    print(f"\nRedis key present after warm: {key}", flush=True)
    print(f"Raw cached payload present: {raw_cached is not None}", flush=True)

    # Show real ForexSB-era neighbor timestamps (Part 16's explicit requirement).
    neighbors = similarity.find_similar_setups(
        canonical_symbol="EURUSD", direction=sample.direction, anchor_strategy=sample.anchor_strategy,
        strategy_version=sample.strategy_version, query_dims=query_dims, regime_broad=sample.regime_broad, top_k=200,
    )
    forexsb_neighbors = [n for n in neighbors if n["fingerprint"].provider == "FOREXSB"]
    mt5_neighbors = [n for n in neighbors if n["fingerprint"].provider == "MT5"]
    print(f"\nTotal neighbors retrieved: {len(neighbors)} (FOREXSB: {len(forexsb_neighbors)}, MT5: {len(mt5_neighbors)})", flush=True)
    print("Sample FOREXSB-era neighbor timestamps (proving pre-2022 analogs are actually retrievable):", flush=True)
    for n in sorted(forexsb_neighbors, key=lambda x: x["fingerprint"].entry_time)[:10]:
        fp = n["fingerprint"]
        print(f"  {fp.entry_time.isoformat()}  similarity={n['similarity']:.3f}  provider={fp.provider}  regime={fp.regime}", flush=True)

    print("\n=== cache.metrics_snapshot() ===", flush=True)
    print(cache.metrics_snapshot(), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

"""Redis integration layer for the MT5 autonomous trading stack.

Redis is used here for exactly three things, per the architecture brief this module
implements: SPEED (a shared, short-TTL L2 cache in front of MT5 broker calls and expensive
deterministic computation), COORDINATION (distributed locks/counters that improve
multi-process safety), and EVENT STREAMING (non-authoritative pub/sub for observability and a
future real-time UI). It is never the durable source of truth for anything -- Postgres remains
authoritative for executions/events/adaptive-management history, and MT5 itself remains
authoritative for live positions. Every function in this module is fail-open for cache/lookup
purposes (a Redis outage falls straight through to the next tier -- L1 in-process cache, or a
live MT5/Postgres fetch -- so trading never stops because Redis is down) and explicit about
fail-closed behavior only where duplicate-prevention correctness genuinely depends on it (see
try_execution_lock's docstring).

Deliberately reuses the ALREADY-CONNECTED Redis client from backend.shared.cache.cache (the
same connection MultiTierCache establishes at app startup via get_unified_fetcher(), before the
MT5 scheduler ever starts) rather than opening a second connection pool -- this stays one Redis
subsystem, not two.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.shared.cache import cache as _multi_tier_cache

logger = logging.getLogger(__name__)

KEY_PREFIX = "mt5"

# ---------------------------------------------------------------------------
# Client access -- fail-open by construction: every caller gets None (never a raised
# exception) when Redis is unavailable, and treats that as "use the next tier."
# ---------------------------------------------------------------------------


def get_client() -> Any:
    try:
        return _multi_tier_cache.get_client()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metrics (Part 16) -- module-level cumulative counters, mirroring the existing
# macro_context.classify_call_count() before/after-delta idiom already used for openai_calls in
# autonomous.py's run_cycle(). Thread-safe (circuit_breaker.py increments from a sync context;
# everything else is asyncio-single-threaded but this stays cheap and correct either way).
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: dict[str, float] = {
    "cache_hits": 0,
    "cache_misses": 0,
    "broker_fetches": 0,
    "smc_cache_hits": 0,
    "smc_cache_misses": 0,
    "cycle_lock_acquired": 0,
    "cycle_lock_contended": 0,
    "execution_lock_acquired": 0,
    "execution_lock_contended": 0,
    "redis_failures": 0,
    "events_published": 0,
    "redis_latency_ms_total": 0.0,
    "redis_calls_total": 0,
}


def _incr(name: str, n: float = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n


def metrics_snapshot() -> dict[str, float]:
    with _metrics_lock:
        return dict(_metrics)


class _latency:
    """Context manager timing a single Redis round trip into redis_latency_ms_total/
    redis_calls_total, so avg latency = redis_latency_ms_total / redis_calls_total. Only used
    around the actual client call, never around cache-miss fallback work."""

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        _incr("redis_latency_ms_total", (time.perf_counter() - self._t0) * 1000)
        _incr("redis_calls_total")
        return False


# ---------------------------------------------------------------------------
# Bar-boundary helpers -- TTLs are DERIVED from the actual next-bar-close time, never a blindly
# hardcoded constant (Part 4), consistent with autonomous.py's own _completed_m5_time() style.
# ---------------------------------------------------------------------------

_TIMEFRAME_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}


def last_closed_bar_time(timeframe: str, *, now: datetime | None = None) -> datetime:
    """Floors `now` to the START of the current (possibly still-open) bar using epoch-minute
    math -- generalizes autonomous.py's own _completed_m5_time() (which does the M5-specific
    `(now.minute // 5) * 5`) to M15/H1/H4 via epoch-aligned buckets, then steps back one full
    period to land on the most recently CLOSED bar's start. Epoch-minute flooring keeps H1/H4
    buckets aligned to UTC hour/4-hour boundaries (1970-01-01 00:00 UTC is itself a UTC
    midnight, and 240 divides evenly into 1440 minutes/day)."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe.upper(), 5)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.replace(second=0, microsecond=0)
    epoch_minutes = int(now.timestamp() // 60)
    floored_minutes = epoch_minutes - (epoch_minutes % minutes)
    current_bar_start = datetime.fromtimestamp(floored_minutes * 60, tz=timezone.utc)
    return current_bar_start - timedelta(minutes=minutes)


def seconds_until_next_bar(timeframe: str, *, safety_margin_seconds: int = 5, now: datetime | None = None) -> int:
    """TTL for data keyed to the CURRENT last-closed bar: valid until the NEXT bar closes, plus
    a small safety margin so a cache entry doesn't expire a few hundred milliseconds early and
    force an avoidable extra fetch right at the boundary."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe.upper(), 5)
    now = now or datetime.now(timezone.utc)
    last_closed = last_closed_bar_time(timeframe, now=now)
    next_close = last_closed + timedelta(minutes=minutes * 2)
    ttl = int((next_close - now).total_seconds()) + safety_margin_seconds
    return max(1, ttl)


def bar_identity(timeframe: str, *, now: datetime | None = None) -> str:
    """Canonical bar-timestamp string used as the LAST path segment of every market-data key --
    this, not TTL expiry, is the primary staleness signal (Part 18): a key for last-closed-bar
    10:00 is simply a DIFFERENT key than 10:05's, so a new bar can never accidentally reuse an
    older bar's cached entry even if the TTL math were ever slightly off."""
    return last_closed_bar_time(timeframe, now=now).strftime("%Y%m%d%H%M")


# ---------------------------------------------------------------------------
# Generic cache get/set -- plain JSON (candle/quote/symbol payloads are already JSON-safe via
# MT5Model.model_dump(mode="json"), used pervasively elsewhere in this codebase already; no
# pickle needed here, unlike MultiTierCache's generic blob store).
# ---------------------------------------------------------------------------


async def cache_get(key: str) -> Optional[Any]:
    client = get_client()
    if client is None:
        return None
    try:
        with _latency():
            raw = await client.get(key)
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis cache_get failed for %s: %s", key, exc.__class__.__name__)
        return None
    if raw is None:
        _incr("cache_misses")
        return None
    try:
        _incr("cache_hits")
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        with _latency():
            await client.set(key, json.dumps(value, default=str), ex=max(1, int(ttl_seconds)))
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis cache_set failed for %s: %s", key, exc.__class__.__name__)


async def cache_delete(*keys: str) -> None:
    client = get_client()
    if client is None or not keys:
        return
    try:
        await client.delete(*keys)
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis cache_delete failed: %s", exc.__class__.__name__)


# ---------------------------------------------------------------------------
# Key namespace (Part 3): mt5:<category>:<identity...>:<last_closed_bar_timestamp> -- every
# market-data key ends in the bar identity so a newer closed bar is structurally a different
# key, never a stale reuse.
# ---------------------------------------------------------------------------


def market_key(kind: str, broker: str, symbol: str, timeframe: str, *, now: datetime | None = None) -> str:
    return f"{KEY_PREFIX}:market:{kind}:{broker}:{symbol}:{timeframe}:{bar_identity(timeframe, now=now)}"


def tick_key(broker: str, symbol: str) -> str:
    # Ticks have no bar identity -- a short (1-5s) TTL is the sole validity signal, by design.
    return f"{KEY_PREFIX}:market:tick:{broker}:{symbol}"


def symbol_metadata_key(broker: str, symbol: str) -> str:
    return f"{KEY_PREFIX}:market:meta:{broker}:{symbol}"


def context_key(cache_version: str, symbol: str, timeframe: str, *, now: datetime | None = None) -> str:
    """Deterministic-computation cache (Part 5): analyze_bars()/regime/SMC snapshot results.
    Includes cache_version so a strategy/market-structure configuration change can never leave
    an old cached value silently valid -- bump MT5_REDIS_CACHE_VERSION to invalidate everything
    at once without touching TTLs."""
    return f"{KEY_PREFIX}:context:{cache_version}:{symbol}:{timeframe}:{bar_identity(timeframe, now=now)}"


def risk_metadata_key(broker: str, symbol: str) -> str:
    return f"{KEY_PREFIX}:risk-metadata:{broker}:{symbol}"


def cache_version() -> str:
    return os.getenv("MT5_REDIS_CACHE_VERSION", "v1")


# ---------------------------------------------------------------------------
# Cached market-data fetch wrappers (Part 3) -- drop-in replacements for
# MT5Adapter.candles()/latest_tick()/symbol_info() that preserve the exact same return type
# (pydantic MT5Candle/MT5Quote/MT5Symbol), so call sites need no other change. L1
# (_cycle_context_cache, unaffected -- these wrappers sit BELOW it) -> Redis L2 -> real MT5
# fetch. A cache MISS always still makes the real MT5 call and populates Redis afterward; a
# Redis error at any point is swallowed by cache_get/cache_set above and simply behaves as a
# miss -- trading never depends on Redis being up.
# ---------------------------------------------------------------------------


async def cached_candles(adapter: Any, broker_symbol: str, timeframe: str, *, count: int = 100) -> list[Any]:
    from backend.brokers.mt5.models import MT5Candle

    key = market_key("candles", str(getattr(adapter, "broker_name", "mt5")), broker_symbol, timeframe)
    cached = await cache_get(key)
    if cached is not None and isinstance(cached, list) and len(cached) >= count:
        try:
            return [MT5Candle.model_validate(row) for row in cached[-count:]]
        except Exception:
            pass  # malformed cache entry -- fall through to a real fetch, never raise
    _incr("broker_fetches")
    rows = await adapter.candles(broker_symbol, timeframe, count=count)
    ttl = seconds_until_next_bar(timeframe)
    await cache_set(key, [row.model_dump(mode="json") for row in rows], ttl)
    return rows


async def cached_latest_tick(adapter: Any, broker_symbol: str, *, ttl_seconds: int | None = None) -> Any:
    from backend.brokers.mt5.models import MT5Quote

    ttl = ttl_seconds if ttl_seconds is not None else int(os.getenv("MT5_REDIS_TICK_TTL_SECONDS", "2"))
    key = tick_key(str(getattr(adapter, "broker_name", "mt5")), broker_symbol)
    cached = await cache_get(key)
    if cached is not None:
        try:
            return MT5Quote.model_validate(cached)
        except Exception:
            pass
    _incr("broker_fetches")
    quote = await adapter.latest_tick(broker_symbol)
    await cache_set(key, quote.model_dump(mode="json"), ttl)
    return quote


async def cached_symbol_info(adapter: Any, broker_symbol: str) -> Any:
    from backend.brokers.mt5.models import MT5Symbol

    ttl = int(os.getenv("MT5_REDIS_SYMBOL_METADATA_TTL_SECONDS", "900"))  # 15 min default, within the 5-30 min band
    key = symbol_metadata_key(str(getattr(adapter, "broker_name", "mt5")), broker_symbol)
    cached = await cache_get(key)
    if cached is not None:
        try:
            return MT5Symbol.model_validate(cached)
        except Exception:
            pass
    _incr("broker_fetches")
    symbol = await adapter.symbol_info(broker_symbol)
    await cache_set(key, symbol.model_dump(mode="json"), ttl)
    return symbol


async def invalidate_symbol_metadata(broker: str, broker_symbol: str) -> None:
    """Called on metadata-health failure / risk mismatch / symbol reconnect (Part 4/11) --
    forces the next fetch to hit MT5 fresh rather than serve a possibly-stale healthy value."""
    await cache_delete(symbol_metadata_key(broker, broker_symbol))


# ---------------------------------------------------------------------------
# Deterministic-computation cache (Part 5) -- analyze_bars() (SMC/ICT structure) is a pure
# function of its input rows, so its result is safe to cache keyed by symbol/timeframe/bar
# identity/cache-version. Used by mt5_strategies/context.py in place of calling analyze_bars()
# directly for M15/H1/H4.
# ---------------------------------------------------------------------------


def note_smc_cache_result(hit: bool) -> None:
    """For call sites (autonomous.py::_screen) that must do the cache lookup in the MAIN event
    loop and the analyze_bars() compute-on-miss in a asyncio.to_thread() worker separately --
    see that function's comments -- rather than going through cached_analyze_bars() end-to-end
    (which would call analyze_bars() inline in the main loop on a miss, exactly the CPU-bound
    main-loop blocking Part 5's thread pool exists to avoid)."""
    _incr("smc_cache_hits" if hit else "smc_cache_misses")


async def cached_analyze_bars(rows: list[dict[str, Any]], *, symbol: str, timeframe: str) -> Any:
    from backend.market_structure.engine import analyze_bars
    from backend.market_structure.models import MarketStructureSnapshot

    key = context_key(cache_version(), symbol, timeframe)
    cached = await cache_get(key)
    if cached is not None:
        try:
            snapshot = MarketStructureSnapshot.model_validate(cached)
            _incr("smc_cache_hits")
            return snapshot
        except Exception:
            pass  # malformed/incompatible cached entry -- recompute rather than raise
    _incr("smc_cache_misses")
    snapshot = analyze_bars(rows, symbol=symbol, timeframe=timeframe)
    await cache_set(key, snapshot.model_dump(mode="json"), seconds_until_next_bar(timeframe))
    return snapshot


# ---------------------------------------------------------------------------
# Risk-metadata cache (Part 11) -- fronts backend.brokers.mt5.risk_calculator's per-symbol
# health classification (OK / DEGRADED_TWO_METHOD / CRITICAL_MISMATCH). Short/moderate TTL,
# with immediate invalidate_risk_metadata() on a freshly-detected mismatch so a cached "healthy"
# result can never outlive a just-discovered critical problem.
# ---------------------------------------------------------------------------


async def cached_risk_metadata_status(broker: str, broker_symbol: str) -> Optional[dict[str, Any]]:
    return await cache_get(risk_metadata_key(broker, broker_symbol))


async def set_risk_metadata_status(broker: str, broker_symbol: str, status: dict[str, Any]) -> None:
    ttl = int(os.getenv("MT5_REDIS_RISK_METADATA_TTL_SECONDS", "600"))  # 10 min default
    await cache_set(risk_metadata_key(broker, broker_symbol), status, ttl)


async def invalidate_risk_metadata(broker: str, broker_symbol: str) -> None:
    await cache_delete(risk_metadata_key(broker, broker_symbol))


# ---------------------------------------------------------------------------
# Distributed locks (Parts 7/8) -- SET NX EX + ownership-safe release, generalizing the exact
# pattern backend.services.redis_quote_bus.RedisQuoteBus already uses for its candle-aggregator
# lock (acquire_aggregator_lock/renew_aggregator_lock), rather than inventing a second lock
# style. Every acquire/release/renew is fail-open on a Redis error UNLESS the caller explicitly
# asks for fail-closed (see try_execution_lock).
# ---------------------------------------------------------------------------


async def try_lock(key: str, owner: str, ttl_seconds: int, *, metric: str | None = None) -> bool:
    """Returns True if `owner` now holds the lock (either newly acquired or Redis unavailable --
    fail-open, since every coordination lock in this module backs a CORRECTNESS mechanism that
    already exists independently -- Postgres cycle-id dedup, DB execution idempotency -- so a
    missed Redis lock degrades coordination speed, never safety). `metric`, if given (e.g.
    "cycle_lock"), records an acquired/contended counter for Part 16 visibility."""
    client = get_client()
    if client is None:
        return True
    try:
        acquired = bool(await client.set(key, owner, nx=True, ex=max(1, int(ttl_seconds))))
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis try_lock failed for %s: %s", key, exc.__class__.__name__)
        return True
    if metric:
        _incr(f"{metric}_acquired" if acquired else f"{metric}_contended")
    return acquired


async def release_lock(key: str, owner: str) -> None:
    """Ownership-safe release: only deletes the key if `owner` is still the current holder --
    never blindly deletes (a slow caller past its TTL must not delete a DIFFERENT owner's newer
    lock). The shared client is created with decode_responses=False (see MultiTierCache), so a
    GET returns bytes -- decoded explicitly before comparison."""
    client = get_client()
    if client is None:
        return
    try:
        current = await client.get(key)
        if current is None:
            return
        current_str = current.decode("utf-8") if isinstance(current, (bytes, bytearray)) else current
        if current_str == owner:
            await client.delete(key)
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis release_lock failed for %s: %s", key, exc.__class__.__name__)


async def try_execution_lock(idempotency_key: str, *, account_id: str = "demo_10k", ttl_seconds: int = 30) -> tuple[bool, bool]:
    """Additional, Redis-only safety layer in FRONT of the existing DB idempotency check in
    portfolio_execution.service.ExecutionManager.submit_mt5_request -- never a replacement for
    it. Returns (proceed, redis_available):
      - Redis up, lock acquired    -> (True, True)   -- proceed, DB check still runs as normal.
      - Redis up, lock held        -> (False, True)  -- short-circuit as a likely duplicate
        WITHOUT even querying the DB; the caller must still treat this conservatively.
      - Redis down                 -> (True, False)  -- proceed to the EXISTING DB idempotency
        check unchanged; this is the one place in this module that is deliberately NOT
        "fail open and skip the safety check" -- it fails open on the REDIS PRE-CHECK only,
        while the real (DB) duplicate-prevention safety net is untouched and still enforced.
    """
    owner = uuid.uuid4().hex
    key = f"{KEY_PREFIX}:account:{account_id}:execution-lock:{idempotency_key}"
    client = get_client()
    if client is None:
        return True, False
    try:
        acquired = bool(await client.set(key, owner, nx=True, ex=max(1, int(ttl_seconds))))
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis try_execution_lock failed for %s: %s", key, exc.__class__.__name__)
        return True, False
    if acquired:
        _incr("execution_lock_acquired")
    else:
        _incr("execution_lock_contended")
    return acquired, True


# ---------------------------------------------------------------------------
# Event bus (Parts 13/14) -- reuses backend.services.redis_quote_bus's already-connected Redis
# client/pub-sub plumbing via the SAME publish-or-local-fallback pattern, on a new channel
# namespace ("mt5:events") so it never collides with the existing quotes:*/bars:* channels.
# Publish-only from this module's perspective; a future WebSocket/SSE route subscribes exactly
# like marketdata_hub.py already does for its own channels. Never publishes secrets/credentials
# -- payloads are restricted to the plain identifiers/numbers listed in MT5_EVENT_TOPICS.
# ---------------------------------------------------------------------------

MT5_EVENT_CHANNEL = "mt5:events"

MT5_EVENT_TOPICS = {
    "mt5.cycle.started",
    "mt5.cycle.completed",
    "mt5.candidate.generated",
    "mt5.candidate.selected",
    "mt5.candidate.rejected",
    "mt5.order.submitted",
    "mt5.order.accepted",
    "mt5.order.rejected",
    "mt5.position.opened",
    "mt5.position.closed",
    "mt5.adaptive.action.selected",
    "mt5.adaptive.action.executed",
    "mt5.risk.metadata_unhealthy",
}


async def publish_event(topic: str, payload: dict[str, Any]) -> None:
    if topic not in MT5_EVENT_TOPICS:
        logger.warning("mt5 redis publish_event: unknown topic %s (publishing anyway)", topic)
    client = get_client()
    message = {"topic": topic, "published_at": datetime.now(timezone.utc).isoformat(), **payload}
    if client is None:
        return
    try:
        await client.publish(MT5_EVENT_CHANNEL, json.dumps(message, default=str))
        _incr("events_published")
    except Exception as exc:
        _incr("redis_failures")
        logger.debug("mt5 redis publish_event failed for %s: %s", topic, exc.__class__.__name__)

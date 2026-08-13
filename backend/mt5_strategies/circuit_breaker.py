"""Per-strategy, demo-only operational circuit breaker (Part 17).

This stops obviously MALFUNCTIONING strategy behavior -- repeated evaluation exceptions,
geometrically impossible SL/TP, non-finite/non-positive prices, absurd reward:risk -- never a
strategy that is simply losing trades. Trading losses are expected and must never trip this.

State is in-memory-FIRST, per-process (unchanged, still the fast/authoritative-for-THIS-process
path), with a Redis hybrid mirror (Redis integration Part 10) so a trip is VISIBLE to other
backend workers and SURVIVES an ordinary process restart, bounded by CIRCUIT_BREAKER_REDIS_TTL_
SECONDS (default 4h) -- this stays an OPERATIONAL breaker, not a permanent ban, even across the
Redis layer. Uses the classic SYNC redis-py client (not the async client every other Redis
integration point in this codebase shares), deliberately: evaluate_all() -- and therefore every
function here -- runs inside backend.brokers.mt5.autonomous.py's asyncio.to_thread() worker
pool, a plain OS thread with NO event loop of its own; calling the shared ASYNC redis client
from there would reuse a connection pool bound to a DIFFERENT (the main loop's) event loop, a
known unsafe cross-loop hazard. A blocking sync socket call has no such problem. This is the one
deliberate exception to "one Redis subsystem, not two" in this integration -- justified by a
real thread-safety constraint, not convenience.

Connection reuse reviewed (Redis integration follow-up): grepped the whole backend for any
other `redis.Redis(...)` / sync client usage to consolidate with -- there is none; every other
Redis touchpoint in this codebase (MultiTierCache, RedisQuoteBus) is async-only. _get_sync_redis
below is therefore already the SINGLE shared sync client for the entire process: one lazily-
created redis-py Redis instance (itself internally connection-pooled and thread-safe for
concurrent use -- redis-py's default ConnectionPool, bounded here via max_connections, hands
out/returns connections safely across threads), reused by every worker thread and every
strategy, never one connection per call or per thread. There is nothing left to consolidate;
kept as designed.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.mt5_strategies.models import StrategySignal

CONSECUTIVE_ERROR_TRIP_THRESHOLD = 5
MALFORMED_SIGNAL_TRIP_THRESHOLD = 5
MALFORMED_WINDOW = timedelta(minutes=15)
DUPLICATE_ATTEMPT_TRIP_THRESHOLD = 5
DUPLICATE_WINDOW = timedelta(minutes=5)

_lock = threading.Lock()
_consecutive_errors: dict[str, int] = defaultdict(int)
_malformed_timestamps: dict[str, list[datetime]] = defaultdict(list)
_duplicate_timestamps: dict[str, list[datetime]] = defaultdict(list)
_tripped: dict[str, dict[str, Any]] = {}

# Rate-limits how often a strategy that is NOT tripped locally re-checks Redis for a trip
# recorded by another process/a previous run of this one -- bounds Redis round trips to at most
# one per strategy per this interval in the (overwhelmingly common) steady-state "nothing
# tripped" case, rather than a Redis call on every single evaluation.
_REDIS_SYNC_INTERVAL_SECONDS = 30.0
_last_redis_check: dict[str, float] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Sync Redis client (see module docstring for why sync, not the shared async client)
# ---------------------------------------------------------------------------

_sync_redis_client: Any = None
_sync_redis_init_lock = threading.Lock()
_sync_redis_unavailable = False


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
            # max_connections bounds the pool this single shared client hands out to concurrent
            # worker threads (see module docstring) -- comfortably above _MULTI_STRATEGY_
            # CONCURRENCY (default 6 concurrent analyze_all() workers) without being unbounded.
            client = redis_sync.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5, max_connections=20)
            client.ping()
            _sync_redis_client = client
        except Exception:
            _sync_redis_unavailable = True
            return None
    return _sync_redis_client


def _redis_key(strategy_id: str) -> str:
    return f"mt5:circuit-breaker:{strategy_id}"


def _redis_ttl_seconds() -> int:
    return int(os.getenv("CIRCUIT_BREAKER_REDIS_TTL_SECONDS", "14400"))  # 4h default


def _mirror_trip_to_redis(strategy_id: str, info: dict[str, Any]) -> None:
    client = _get_sync_redis()
    if client is None:
        return
    try:
        import json

        client.set(_redis_key(strategy_id), json.dumps(info), ex=_redis_ttl_seconds())
    except Exception:
        pass  # best-effort mirror -- local state (already set) remains authoritative for this process


def _mirror_reset_to_redis(strategy_id: str) -> None:
    client = _get_sync_redis()
    if client is None:
        return
    try:
        client.delete(_redis_key(strategy_id))
    except Exception:
        pass


def _check_redis_for_trip(strategy_id: str) -> dict[str, Any] | None:
    """Rate-limited (see _REDIS_SYNC_INTERVAL_SECONDS): only actually calls Redis at most once
    per strategy per interval when this process doesn't already know about a trip locally."""
    now = time.monotonic()
    last = _last_redis_check.get(strategy_id, 0.0)
    if now - last < _REDIS_SYNC_INTERVAL_SECONDS:
        return None
    _last_redis_check[strategy_id] = now
    client = _get_sync_redis()
    if client is None:
        return None
    try:
        import json

        raw = client.get(_redis_key(strategy_id))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _trip(strategy_id: str, reason: str) -> None:
    if strategy_id not in _tripped:
        info = {"reason": reason, "tripped_at": _now().isoformat()}
        _tripped[strategy_id] = info
        _mirror_trip_to_redis(strategy_id, info)


def is_tripped(strategy_id: str) -> bool:
    with _lock:
        if strategy_id in _tripped:
            return True
    # Not tripped in THIS process's memory -- rate-limited check whether another process (or an
    # earlier run of this same one, before a restart) already tripped it.
    remote = _check_redis_for_trip(strategy_id)
    if remote is not None:
        with _lock:
            _tripped.setdefault(strategy_id, remote)
        return True
    return False


def trip_info(strategy_id: str) -> dict[str, Any] | None:
    with _lock:
        info = _tripped.get(strategy_id)
        return dict(info) if info else None


def reset(strategy_id: str) -> None:
    """Manual operator reset -- clears trip state and all counters for one strategy, locally
    AND in the shared Redis mirror (a reset that only cleared this process would leave every
    OTHER worker still treating the strategy as tripped)."""
    with _lock:
        _tripped.pop(strategy_id, None)
        _consecutive_errors.pop(strategy_id, None)
        _malformed_timestamps.pop(strategy_id, None)
        _duplicate_timestamps.pop(strategy_id, None)
    _mirror_reset_to_redis(strategy_id)


def reset_all() -> None:
    with _lock:
        strategy_ids = list(_tripped.keys())
        _tripped.clear()
        _consecutive_errors.clear()
        _malformed_timestamps.clear()
        _duplicate_timestamps.clear()
    for strategy_id in strategy_ids:
        _mirror_reset_to_redis(strategy_id)


def record_evaluation_error(strategy_id: str) -> None:
    with _lock:
        _consecutive_errors[strategy_id] += 1
        if _consecutive_errors[strategy_id] >= CONSECUTIVE_ERROR_TRIP_THRESHOLD:
            _trip(strategy_id, "REPEATED_EVALUATION_ERRORS")


def record_evaluation_success(strategy_id: str) -> None:
    with _lock:
        _consecutive_errors[strategy_id] = 0


def _prune(timestamps: list[datetime], window: timedelta) -> list[datetime]:
    cutoff = _now() - window
    return [ts for ts in timestamps if ts >= cutoff]


def record_malformed_signal(strategy_id: str) -> None:
    with _lock:
        pruned = _prune(_malformed_timestamps[strategy_id], MALFORMED_WINDOW)
        pruned.append(_now())
        _malformed_timestamps[strategy_id] = pruned
        if len(pruned) >= MALFORMED_SIGNAL_TRIP_THRESHOLD:
            _trip(strategy_id, "MALFORMED_SIGNAL_GENERATION")


def record_duplicate_order_attempt(strategy_id: str) -> None:
    """Excessive duplicate-order attempts within a short window are an operational bug
    signature (e.g. a strategy re-submitting the same idempotency key), not a performance
    concern -- distinct from the existing, unrelated idempotency dedup in execution.py, which
    prevents the duplicate from ever reaching the broker regardless of this breaker."""
    with _lock:
        pruned = _prune(_duplicate_timestamps[strategy_id], DUPLICATE_WINDOW)
        pruned.append(_now())
        _duplicate_timestamps[strategy_id] = pruned
        if len(pruned) >= DUPLICATE_ATTEMPT_TRIP_THRESHOLD:
            _trip(strategy_id, "DUPLICATE_ORDER_ATTEMPTS")


_MAX_SANE_REWARD_RISK = 20.0


def validate_signal_sanity(signal: StrategySignal) -> str | None:
    """Hard geometric/numeric sanity check, independent of each strategy's own reward:risk
    floor logic -- this exists to catch a BUG producing an impossible signal (stop on the
    wrong side of entry, non-finite price, absurd RR), not to second-guess a valid but
    unfavourable setup. Returns a rejection reason string when malformed, else None."""
    if signal is None or not signal.valid:
        return None
    entry, stop, target = signal.proposed_entry, signal.stop_loss, signal.take_profit
    if entry is None or stop is None or target is None:
        return "MALFORMED_MISSING_PRICE"
    for value in (entry, stop, target):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return "MALFORMED_NON_FINITE_PRICE"
        if value <= 0:
            return "MALFORMED_NON_POSITIVE_PRICE"
    if signal.direction == "LONG" and not (stop < entry < target):
        return "MALFORMED_GEOMETRY"
    if signal.direction == "SHORT" and not (target < entry < stop):
        return "MALFORMED_GEOMETRY"
    if signal.reward_risk is not None:
        if not math.isfinite(signal.reward_risk) or signal.reward_risk <= 0 or signal.reward_risk > _MAX_SANE_REWARD_RISK:
            return "MALFORMED_IMPOSSIBLE_RR"
    return None


def circuit_breaker_status() -> dict[str, Any]:
    with _lock:
        return {
            "tripped": {sid: dict(info) for sid, info in _tripped.items()},
            "consecutive_errors": {sid: count for sid, count in _consecutive_errors.items() if count > 0},
            "malformed_signal_counts_in_window": {sid: len(_prune(ts, MALFORMED_WINDOW)) for sid, ts in _malformed_timestamps.items() if _prune(ts, MALFORMED_WINDOW)},
            "duplicate_attempt_counts_in_window": {sid: len(_prune(ts, DUPLICATE_WINDOW)) for sid, ts in _duplicate_timestamps.items() if _prune(ts, DUPLICATE_WINDOW)},
        }

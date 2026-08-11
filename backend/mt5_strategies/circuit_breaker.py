"""Per-strategy, demo-only operational circuit breaker (Part 17).

This stops obviously MALFUNCTIONING strategy behavior -- repeated evaluation exceptions,
geometrically impossible SL/TP, non-finite/non-positive prices, absurd reward:risk -- never a
strategy that is simply losing trades. Trading losses are expected and must never trip this.

State is in-memory, per-process, reset on backend restart. That is a deliberate scope choice:
a restart already implies operator attention, and it avoids adding a persisted table for a
mechanism whose entire job is "stop a bug, right now" rather than track history across
restarts. `circuit_breaker_status()` exposes current state for API visibility (Part 16 --
"config/API visibility is enough, no web UI").
"""
from __future__ import annotations

import math
import threading
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _trip(strategy_id: str, reason: str) -> None:
    if strategy_id not in _tripped:
        _tripped[strategy_id] = {"reason": reason, "tripped_at": _now().isoformat()}


def is_tripped(strategy_id: str) -> bool:
    with _lock:
        return strategy_id in _tripped


def trip_info(strategy_id: str) -> dict[str, Any] | None:
    with _lock:
        info = _tripped.get(strategy_id)
        return dict(info) if info else None


def reset(strategy_id: str) -> None:
    """Manual operator reset -- clears trip state and all counters for one strategy."""
    with _lock:
        _tripped.pop(strategy_id, None)
        _consecutive_errors.pop(strategy_id, None)
        _malformed_timestamps.pop(strategy_id, None)
        _duplicate_timestamps.pop(strategy_id, None)


def reset_all() -> None:
    with _lock:
        _tripped.clear()
        _consecutive_errors.clear()
        _malformed_timestamps.clear()
        _duplicate_timestamps.clear()


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

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
    DISABLED = "DISABLED"


@dataclass
class ProviderHealth:
    provider: str
    configured: bool
    enabled: bool = True
    healthy: bool = True
    state: CircuitState = CircuitState.CLOSED
    last_success: str | None = None
    last_failure: str | None = None
    latency_ms: float | None = None
    failure_count: int = 0
    opened_until: float = 0.0


class ProviderCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, open_seconds: int = 60) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._health: dict[str, ProviderHealth] = {}

    def status(self, provider: str, *, configured: bool = False) -> ProviderHealth:
        row = self._health.setdefault(provider, ProviderHealth(provider=provider, configured=configured))
        row.configured = configured
        if row.state == CircuitState.OPEN and row.opened_until <= time.time():
            row.state = CircuitState.HALF_OPEN
        return row

    def before_request(self, provider: str, *, configured: bool) -> ProviderHealth:
        row = self.status(provider, configured=configured)
        if not row.enabled:
            row.state = CircuitState.DISABLED
            raise RuntimeError("provider disabled")
        if row.state == CircuitState.OPEN:
            raise RuntimeError("provider circuit open")
        return row

    def success(self, provider: str, *, latency_ms: float) -> None:
        row = self.status(provider)
        row.healthy = True
        row.state = CircuitState.CLOSED
        row.failure_count = 0
        row.latency_ms = latency_ms
        row.last_success = _now()

    def failure(self, provider: str, *, reason: str) -> None:
        row = self.status(provider)
        row.healthy = False
        row.failure_count += 1
        row.last_failure = reason
        if row.failure_count >= self.failure_threshold:
            row.state = CircuitState.OPEN
            row.opened_until = time.time() + self.open_seconds

    def as_dict(self) -> dict[str, Any]:
        return {key: {**value.__dict__, "state": value.state.value} for key, value in self._health.items()}


def retry_delay(attempt: int) -> float:
    return min(4.0, 0.25 * (2**attempt)) + random.uniform(0, 0.05)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


circuit_breaker = ProviderCircuitBreaker()

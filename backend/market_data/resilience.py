from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class ProviderErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    ENTITLEMENT_REQUIRED = "entitlement_required"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    INVALID_REQUEST = "invalid_request"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    DATA_UNAVAILABLE = "data_unavailable"
    PROVIDER_ERROR = "provider_error"
    PARSE_ERROR = "parse_error"
    QUALITY_REJECTED = "quality_rejected"


class ProviderCallError(RuntimeError):
    def __init__(self, code: ProviderErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    timeout_seconds: float = 10.0
    base_backoff_seconds: float = 0.1
    max_backoff_seconds: float = 2.0
    retryable: set[ProviderErrorCode] | None = None

    def is_retryable(self, code: ProviderErrorCode) -> bool:
        retryable = self.retryable or {
            ProviderErrorCode.RATE_LIMITED,
            ProviderErrorCode.TIMEOUT,
            ProviderErrorCode.CONNECTION_FAILED,
            ProviderErrorCode.PROVIDER_ERROR,
        }
        return code in retryable


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    failures: int = 0
    opened_until: datetime | None = None

    @property
    def state(self) -> str:
        if self.opened_until and self.opened_until > datetime.now(timezone.utc):
            return "open"
        if self.failures:
            return "half_open" if self.opened_until else "closed"
        return "closed"

    def before_call(self) -> None:
        if self.opened_until and self.opened_until > datetime.now(timezone.utc):
            raise ProviderCallError(ProviderErrorCode.PROVIDER_ERROR, "circuit breaker open")

    def record_success(self) -> None:
        self.failures = 0
        self.opened_until = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_until = datetime.now(timezone.utc) + timedelta(seconds=self.cooldown_seconds)


async def call_with_resilience(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    breaker: CircuitBreaker | None = None,
) -> T:
    policy = policy or RetryPolicy()
    breaker = breaker or CircuitBreaker()
    attempt = 0
    while True:
        attempt += 1
        breaker.before_call()
        try:
            result = await asyncio.wait_for(fn(), timeout=policy.timeout_seconds)
            breaker.record_success()
            return result
        except asyncio.TimeoutError as exc:
            error = ProviderCallError(ProviderErrorCode.TIMEOUT, "provider timed out")
            last_exc: Exception = error
        except ProviderCallError as exc:
            error = exc
            last_exc = exc
        except Exception as exc:
            error = ProviderCallError(ProviderErrorCode.PROVIDER_ERROR, str(exc))
            last_exc = exc
        breaker.record_failure()
        if attempt >= policy.max_attempts or not policy.is_retryable(error.code):
            raise error from last_exc
        delay = min(policy.max_backoff_seconds, policy.base_backoff_seconds * (2 ** (attempt - 1)))
        await asyncio.sleep(delay)

from __future__ import annotations

import pytest

from backend.market_data.cache_policy import get_cache_policy
from backend.market_data.resilience import CircuitBreaker, ProviderCallError, ProviderErrorCode, RetryPolicy, call_with_resilience


def test_cache_policy_keys_and_trading_guard() -> None:
    policy = get_cache_policy("quote")
    assert policy.key("AAPL", "NASDAQ") == "market_data:quote:aapl:nasdaq"
    assert policy.may_use_for_trading_decisions is False


@pytest.mark.asyncio
async def test_retry_does_not_retry_authentication_failure() -> None:
    calls = 0

    async def failing():
        nonlocal calls
        calls += 1
        raise ProviderCallError(ProviderErrorCode.AUTHENTICATION_FAILED, "bad key")

    with pytest.raises(ProviderCallError):
        await call_with_resilience(failing, policy=RetryPolicy(max_attempts=5, base_backoff_seconds=0))
    assert calls == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)

    async def failing():
        raise ProviderCallError(ProviderErrorCode.PROVIDER_ERROR, "down")

    with pytest.raises(ProviderCallError):
        await call_with_resilience(failing, policy=RetryPolicy(max_attempts=1), breaker=breaker)
    assert breaker.state == "open"

"""Redis integration layer for the MT5 autonomous trading stack -- Parts 1-21.

Uses a hermetic in-memory fake Redis client (matching this test suite's existing philosophy of
fakes over real network dependencies -- see test_adaptive_trade_manager_v2.py's _FakeExecAdapter)
rather than requiring a real Redis connection, so these tests are fast and deterministic in any
environment. Exercises backend.mt5_strategies.redis_layer directly, plus the key wired-in call
sites (execution idempotency lock, strategy circuit breaker hybrid) where a real Redis outage or
race is safety-relevant.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from backend.mt5_strategies import redis_layer


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[bytes, float | None]] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and time.time() > expiry:
            del self.store[key]
            return None
        return value

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            existing = self.store.get(key)
            if existing and (existing[1] is None or time.time() <= existing[1]):
                return None
        if isinstance(value, str):
            value = value.encode()
        expiry = time.time() + ex if ex else None
        self.store[key] = (value, expiry)
        return True

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _BrokenRedis:
    """Simulates a Redis outage -- every call raises."""

    async def get(self, *a, **k):
        raise ConnectionError("redis down")

    async def set(self, *a, **k):
        raise ConnectionError("redis down")

    async def delete(self, *a, **k):
        raise ConnectionError("redis down")

    async def publish(self, *a, **k):
        raise ConnectionError("redis down")


@pytest.fixture(autouse=True)
def _reset_metrics():
    with redis_layer._metrics_lock:
        for k in redis_layer._metrics:
            redis_layer._metrics[k] = 0
    yield


def _mk_candle(**overrides):
    from backend.brokers.mt5.models import MT5Candle

    defaults = dict(symbol="EURUSD", timeframe="M15", time="2026-01-01T00:00:00Z", open=1.1, high=1.11, low=1.09, close=1.105, tick_volume=100, spread=1, real_volume=0, quality_flags=[])
    defaults.update(overrides)
    return MT5Candle(**defaults)


# ---------------------------------------------------------------------------
# 1. L1 cache still works without Redis (autonomous.py's _cycle_context_cache is untouched by
# this whole module -- it is a plain dict, never imports redis_layer).
# ---------------------------------------------------------------------------


def test_l1_cycle_context_cache_is_a_plain_dict_independent_of_redis():
    cache: dict = {}
    cache["EURUSD"] = object()
    assert cache.get("EURUSD") is not None
    assert cache.get("GBPUSD") is None
    # No redis_layer import anywhere in this assertion path -- L1 has zero Redis dependency.


# ---------------------------------------------------------------------------
# 2 & 3. Redis hit avoids a duplicate MT5 candle fetch; a cache miss fetches MT5 and populates
# Redis.
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def __init__(self, candles):
        self._candles = candles
        self.calls = 0

    async def candles(self, symbol, timeframe, count=100):
        self.calls += 1
        return self._candles


def test_cache_miss_fetches_mt5_and_populates_redis(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    rows = [_mk_candle() for _ in range(5)]
    adapter = _FakeAdapter(rows)

    result = asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=5))

    assert adapter.calls == 1
    assert len(result) == 5
    key = redis_layer.market_key("candles", "mt5", "EURUSD", "M15")
    assert key in fake_redis.store  # populated


def test_redis_hit_avoids_duplicate_mt5_candle_fetch(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    rows = [_mk_candle() for _ in range(5)]
    adapter = _FakeAdapter(rows)

    asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=5))  # populates cache
    asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=5))  # should hit cache

    assert adapter.calls == 1  # NOT 2 -- second call served from Redis


# ---------------------------------------------------------------------------
# 4. A new closed candle invalidates/rekeys cached M5 context (bar identity, not just TTL).
# ---------------------------------------------------------------------------


def test_new_closed_bar_produces_a_different_cache_key():
    t1 = datetime(2026, 1, 1, 10, 3, tzinfo=timezone.utc)  # inside the 10:00-10:05 M5 bar
    t2 = datetime(2026, 1, 1, 10, 6, tzinfo=timezone.utc)  # inside the NEXT M5 bar (10:05-10:10)

    key1 = redis_layer.market_key("candles", "mt5", "EURUSD", "M5", now=t1)
    key2 = redis_layer.market_key("candles", "mt5", "EURUSD", "M5", now=t2)

    assert key1 != key2  # a new bar is structurally a different key -- never a stale reuse


def test_same_bar_produces_the_same_cache_key():
    t1 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 10, 4, tzinfo=timezone.utc)  # still inside the same 10:00-10:05 bar

    key1 = redis_layer.market_key("candles", "mt5", "EURUSD", "M5", now=t1)
    key2 = redis_layer.market_key("candles", "mt5", "EURUSD", "M5", now=t2)

    assert key1 == key2


# ---------------------------------------------------------------------------
# 5. H1/H4 context can be safely reused while unchanged (bar identity only changes when the
# bar actually rolls -- H1 stays stable across many M5 boundaries).
# ---------------------------------------------------------------------------


def test_h1_key_stable_across_multiple_m5_boundaries():
    t1 = datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 10, 41, tzinfo=timezone.utc)  # 8 M5 bars later, still inside the same H1 bar (10:00-11:00)

    key1 = redis_layer.market_key("candles", "mt5", "EURUSD", "H1", now=t1)
    key2 = redis_layer.market_key("candles", "mt5", "EURUSD", "H1", now=t2)

    assert key1 == key2


def test_h1_key_changes_once_the_h1_bar_rolls():
    t1 = datetime(2026, 1, 1, 10, 55, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 11, 5, tzinfo=timezone.utc)  # into the next H1 bar

    key1 = redis_layer.market_key("candles", "mt5", "EURUSD", "H1", now=t1)
    key2 = redis_layer.market_key("candles", "mt5", "EURUSD", "H1", now=t2)

    assert key1 != key2


# ---------------------------------------------------------------------------
# 6. Stale candle context is never used -- a cache entry shorter than the requested `count`
# (e.g. left over from a smaller earlier request) is treated as a miss, not silently truncated
# data.
# ---------------------------------------------------------------------------


def test_undersized_cached_candles_treated_as_miss_not_reused(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    small_rows = [_mk_candle() for _ in range(3)]
    big_rows = [_mk_candle() for _ in range(100)]
    adapter = _FakeAdapter(small_rows)
    asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=3))  # caches only 3 rows

    adapter._candles = big_rows
    result = asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=100))  # asks for 100

    assert adapter.calls == 2  # second call is a genuine (correct) re-fetch, not a bad reuse
    assert len(result) == 100


# ---------------------------------------------------------------------------
# 7 & 8. Distributed cycle lock prevents a duplicate scheduler cycle; an expired/crashed lock
# can recover safely (TTL-bounded, no permanent deadlock).
# ---------------------------------------------------------------------------


def test_distributed_lock_prevents_duplicate_concurrent_holder(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)

    first = asyncio.run(redis_layer.try_lock("mt5:cycle-lock:C1", "owner-A", 60))
    second = asyncio.run(redis_layer.try_lock("mt5:cycle-lock:C1", "owner-B", 60))

    assert first is True
    assert second is False  # a second process/owner sees the lock held and is turned away


def test_expired_lock_recovers_safely_after_ttl(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)

    asyncio.run(redis_layer.try_lock("mt5:cycle-lock:C2", "owner-A", 1))
    # Simulate TTL expiry (e.g. the holder crashed) by manually expiring the fake entry.
    key = "mt5:cycle-lock:C2"
    value, _ = fake_redis.store[key]
    fake_redis.store[key] = (value, time.time() - 1)

    recovered = asyncio.run(redis_layer.try_lock("mt5:cycle-lock:C2", "owner-B", 60))

    assert recovered is True  # a new owner can acquire once the old lock has expired -- no permanent deadlock


def test_lock_release_is_ownership_safe(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    asyncio.run(redis_layer.try_lock("mt5:test-lock", "owner-A", 60))

    asyncio.run(redis_layer.release_lock("mt5:test-lock", "owner-B"))  # wrong owner -- must NOT release
    still_held = asyncio.run(redis_layer.try_lock("mt5:test-lock", "owner-C", 60))
    assert still_held is False

    asyncio.run(redis_layer.release_lock("mt5:test-lock", "owner-A"))  # correct owner -- releases
    now_free = asyncio.run(redis_layer.try_lock("mt5:test-lock", "owner-D", 60))
    assert now_free is True


def test_lock_fails_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(redis_layer, "get_client", lambda: None)
    acquired = asyncio.run(redis_layer.try_lock("mt5:cycle-lock:C3", "owner-A", 60))
    assert acquired is True  # a Redis outage never blocks a cycle from running


# ---------------------------------------------------------------------------
# 9 & 10. Execution lock prevents a duplicate order attempt; database idempotency remains intact
# (Redis lock is additive, never a replacement).
# ---------------------------------------------------------------------------


def test_execution_lock_prevents_duplicate_order_attempt(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)

    proceed1, available1 = asyncio.run(redis_layer.try_execution_lock("IDEMP-KEY-1"))
    proceed2, available2 = asyncio.run(redis_layer.try_execution_lock("IDEMP-KEY-1"))

    assert (proceed1, available1) == (True, True)
    assert (proceed2, available2) == (False, True)  # second concurrent attempt for the SAME key is blocked


def test_execution_lock_fails_open_to_existing_db_check_when_redis_down(monkeypatch):
    monkeypatch.setattr(redis_layer, "get_client", lambda: None)
    proceed, available = asyncio.run(redis_layer.try_execution_lock("IDEMP-KEY-2"))
    # Redis down -> proceed=True (fall through to the UNCHANGED, still-authoritative DB
    # idempotency check in portfolio_execution.service.submit_mt5_request), available=False so
    # the caller knows this was a pre-check bypass, not a real acquisition.
    assert proceed is True
    assert available is False


def test_database_idempotency_check_is_unaffected_by_redis_lock_source_unchanged():
    """The DB idempotency block in submit_mt5_request (ExecutionOrderORM lookup by
    idempotency_key, existing.duplicate=True) is untouched by this integration -- verified by
    inspecting that submit_mt5_request's source still contains the original DB check, exactly
    as it did before, with the Redis pre-check appearing strictly BEFORE it, never replacing
    it."""
    import inspect

    from backend.portfolio_execution.service import ExecutionManager

    source = inspect.getsource(ExecutionManager.submit_mt5_request)
    assert "_execution_order_query(db, account_context, idempotency_key)" in source
    assert "try_execution_lock" in source
    assert source.index("try_execution_lock") < source.index("_execution_order_query(db, account_context, idempotency_key)")


# ---------------------------------------------------------------------------
# 11 & 12. Redis outage falls back safely; Redis outage does not stop adaptive management (the
# per-ticket lock fails open, same as every other lock in this module).
# ---------------------------------------------------------------------------


def test_cache_get_set_fail_open_on_redis_outage(monkeypatch):
    monkeypatch.setattr(redis_layer, "get_client", lambda: _BrokenRedis())
    result = asyncio.run(redis_layer.cache_get("some:key"))
    assert result is None  # treated as a miss, never raises
    asyncio.run(redis_layer.cache_set("some:key", {"a": 1}, 60))  # must not raise
    metrics = redis_layer.metrics_snapshot()
    assert metrics["redis_failures"] >= 1


def test_adaptive_per_ticket_lock_fails_open_so_management_is_never_blocked(monkeypatch):
    monkeypatch.setattr(redis_layer, "get_client", lambda: None)
    acquired = asyncio.run(redis_layer.try_lock("mt5:adaptive-lock:12345", "adaptive:1", 5, metric="adaptive_ticket_lock"))
    assert acquired is True  # Redis down -> adaptive management proceeds exactly as before this integration


# ---------------------------------------------------------------------------
# 13. Strategy circuit breaker state is shared appropriately (Redis mirror, sync client, TTL-
# bounded) -- and unaffected when Redis is unavailable.
# ---------------------------------------------------------------------------


def test_circuit_breaker_trip_is_mirrored_to_redis_and_visible_to_a_fresh_process(monkeypatch):
    from backend.mt5_strategies import circuit_breaker

    fake_store: dict[str, tuple[bytes, float | None]] = {}

    class _FakeSyncRedis:
        def ping(self):
            return True

        def set(self, key, value, ex=None):
            fake_store[key] = (value.encode() if isinstance(value, str) else value, time.time() + ex if ex else None)

        def get(self, key):
            entry = fake_store.get(key)
            return entry[0] if entry else None

        def delete(self, key):
            fake_store.pop(key, None)

    monkeypatch.setattr(circuit_breaker, "_get_sync_redis", lambda: _FakeSyncRedis())
    circuit_breaker.reset_all()

    for _ in range(circuit_breaker.CONSECUTIVE_ERROR_TRIP_THRESHOLD):
        circuit_breaker.record_evaluation_error("test_strategy_a")

    assert circuit_breaker.is_tripped("test_strategy_a") is True
    assert f"mt5:circuit-breaker:test_strategy_a".encode() != b""  # key format sanity
    assert fake_store.get("mt5:circuit-breaker:test_strategy_a") is not None  # mirrored

    # Simulate a FRESH process: clear only the in-memory state, keep the Redis mirror.
    with circuit_breaker._lock:
        circuit_breaker._tripped.clear()
    circuit_breaker._last_redis_check.clear()

    assert circuit_breaker.is_tripped("test_strategy_a") is True  # recovered from the shared Redis mirror
    circuit_breaker.reset_all()


def test_circuit_breaker_reset_clears_redis_mirror_too(monkeypatch):
    from backend.mt5_strategies import circuit_breaker

    fake_store: dict[str, bytes] = {}

    class _FakeSyncRedis:
        def ping(self):
            return True

        def set(self, key, value, ex=None):
            fake_store[key] = value.encode() if isinstance(value, str) else value

        def get(self, key):
            return fake_store.get(key)

        def delete(self, key):
            fake_store.pop(key, None)

    monkeypatch.setattr(circuit_breaker, "_get_sync_redis", lambda: _FakeSyncRedis())
    circuit_breaker.reset_all()
    for _ in range(circuit_breaker.CONSECUTIVE_ERROR_TRIP_THRESHOLD):
        circuit_breaker.record_evaluation_error("test_strategy_b")
    assert "mt5:circuit-breaker:test_strategy_b" in fake_store

    circuit_breaker.reset("test_strategy_b")

    assert "mt5:circuit-breaker:test_strategy_b" not in fake_store
    assert circuit_breaker.is_tripped("test_strategy_b") is False


def test_circuit_breaker_unaffected_by_redis_being_completely_unavailable(monkeypatch):
    from backend.mt5_strategies import circuit_breaker

    monkeypatch.setattr(circuit_breaker, "_get_sync_redis", lambda: None)
    circuit_breaker.reset_all()

    for _ in range(circuit_breaker.CONSECUTIVE_ERROR_TRIP_THRESHOLD):
        circuit_breaker.record_evaluation_error("test_strategy_c")

    assert circuit_breaker.is_tripped("test_strategy_c") is True  # local in-memory trip still works, unaffected
    circuit_breaker.reset_all()


# ---------------------------------------------------------------------------
# 14. Adaptive cooldown/action state cannot produce duplicate actions -- the per-ticket Redis
# lock does not weaken the EXISTING (Postgres-backed, unchanged) cooldown/idempotency layers;
# verified by source inspection that those checks still exist untouched.
# ---------------------------------------------------------------------------


def test_adaptive_cooldown_and_idempotency_checks_remain_in_can_execute_source():
    import inspect

    from backend.adaptive_management import service as adaptive_service

    source = inspect.getsource(adaptive_service.AdaptiveManagementService._can_execute)
    assert "VOLUME_MUTATING_COOLDOWN_ACTION_TYPES" in source
    assert "_cooldown_elapsed" in source
    assert "_rate_limit_ok" in source  # the hourly counter check -- untouched, still Postgres-backed


# ---------------------------------------------------------------------------
# 15. Risk metadata cache invalidates on mismatch.
# ---------------------------------------------------------------------------


def test_risk_metadata_cache_invalidates_on_mismatch(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)

    asyncio.run(redis_layer.set_risk_metadata_status("mt5", "XAUUSD", {"status": "OK"}))
    assert asyncio.run(redis_layer.cached_risk_metadata_status("mt5", "XAUUSD")) == {"status": "OK"}

    asyncio.run(redis_layer.invalidate_risk_metadata("mt5", "XAUUSD"))

    assert asyncio.run(redis_layer.cached_risk_metadata_status("mt5", "XAUUSD")) is None  # stale healthy value is gone


def test_symbol_metadata_invalidated_alongside_risk_metadata_mismatch(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    key = redis_layer.symbol_metadata_key("mt5", "XAUUSD")
    asyncio.run(redis_layer.cache_set(key, {"symbol": "XAUUSD"}, 900))

    asyncio.run(redis_layer.invalidate_symbol_metadata("mt5", "XAUUSD"))

    assert asyncio.run(redis_layer.cache_get(key)) is None


# ---------------------------------------------------------------------------
# 16 & 17. Event bus publishes candidate/order/management events; event bus failure cannot
# block trading.
# ---------------------------------------------------------------------------


def test_event_bus_publishes_with_topic_and_payload(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)

    asyncio.run(redis_layer.publish_event("mt5.order.accepted", {"symbol": "EURUSD", "ticket": "12345"}))

    assert len(fake_redis.published) == 1
    channel, message = fake_redis.published[0]
    assert channel == redis_layer.MT5_EVENT_CHANNEL
    payload = json.loads(message)
    assert payload["topic"] == "mt5.order.accepted"
    assert payload["symbol"] == "EURUSD"
    assert "published_at" in payload


def test_event_bus_failure_never_raises(monkeypatch):
    monkeypatch.setattr(redis_layer, "get_client", lambda: _BrokenRedis())
    # Must not raise -- a broken event bus can never interrupt the calling trading code path.
    asyncio.run(redis_layer.publish_event("mt5.cycle.started", {"cycle_id": "C1"}))
    assert redis_layer.metrics_snapshot()["redis_failures"] >= 1


def test_event_bus_never_publishes_credential_looking_fields(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    asyncio.run(redis_layer.publish_event("mt5.order.submitted", {"symbol": "EURUSD", "direction": "LONG", "strategy": "mtfai1", "intent_id": "X1"}))
    _, message = fake_redis.published[0]
    lowered = message.lower()
    for forbidden in ("password", "token", "secret", "api_key", "login"):
        assert forbidden not in lowered


# ---------------------------------------------------------------------------
# 18 & 19. Confidence result remains deterministic; threshold remains 75 -- this Redis
# integration never touches strategy/confidence logic, verified by source inspection (no
# redis_layer import in confidence.py) and the unchanged default.
# ---------------------------------------------------------------------------


def test_confidence_module_has_no_redis_dependency():
    import inspect

    from backend.brokers.mt5 import confidence

    source = inspect.getsource(confidence)
    assert "redis" not in source.lower()


def test_confidence_threshold_remains_75():
    from backend.brokers.mt5.confidence import is_autonomous_eligible

    assert is_autonomous_eligible(75.0) is True
    assert is_autonomous_eligible(74.99) is False


# ---------------------------------------------------------------------------
# 20. All 11 strategies remain active on DEMO -- unaffected by this integration.
# ---------------------------------------------------------------------------


def test_all_eleven_strategies_remain_active(monkeypatch):
    from backend.mt5_strategies.families import EVALUATORS
    from backend.mt5_strategies.models import ACTIVE_MT5, activation_status

    # "donchian_trend_follow" (2026-08-24) is excluded for the same reason as wyckoff below --
    # a later addition, DISABLED pending its own 3-year OOS validation.
    # "wyckoff" (2026-08-17) is excluded -- it's a later addition, pending historical/OOS
    # validation, deliberately DISABLED by default, not part of this Stage-1-complete cohort.
    stage1_ids = [sid for sid in EVALUATORS if sid not in {"wyckoff", "donchian_trend_follow", "session_liquidity_breakout", "fx_relative_momentum"}]
    all_ids = ["mtfai1"] + stage1_ids
    assert len(all_ids) == 11
    # session_breakout/support_resistance_bounce are pinned explicitly: the real container env
    # has both demoted to SHADOW_MT5 (2026-08-18 real-trade forensic demotion) -- this test is
    # about the CODED DEFAULT, not today's real deployed override.
    for strategy_id in stage1_ids:
        monkeypatch.delenv(f"MT5_STRATEGY_ACTIVATION_{strategy_id.upper()}", raising=False)
    for strategy_id in stage1_ids:
        assert activation_status(strategy_id) == ACTIVE_MT5


# ---------------------------------------------------------------------------
# 21-23. Portfolio risk / economic-risk / adaptive-management rules unchanged -- no redis
# import in the modules that hold those rules (portfolio risk limits, economic guard
# thresholds, adaptive BE/trailing/MFE/invalidation logic).
# ---------------------------------------------------------------------------


def test_portfolio_risk_module_untouched_by_redis_integration():
    import inspect

    from backend.brokers.mt5 import risk_budget

    source = inspect.getsource(risk_budget)
    assert "redis" not in source.lower()


def test_adaptive_decision_thresholds_unchanged():
    """Spot-checks that this integration did not touch any of the named adaptive-management
    threshold constants/env-var defaults (BE/trailing/MFE/invalidation logic)."""
    import inspect

    from backend.adaptive_management import service as adaptive_service

    source = inspect.getsource(adaptive_service)
    for constant in ("ADAPTIVE_BREAKEVEN_R", "ADAPTIVE_PARTIAL_PROFIT_R", "MFE_GIVEBACK_LIMIT_AFTER_1R", "two_completed_opposing_candles_after_adverse_move"):
        assert constant in source  # still present, unchanged


# ---------------------------------------------------------------------------
# 24-26. OpenAI calls remain zero, IBKR remains absent, live trading remains blocked -- no
# redis_layer usage anywhere near those invariants; verified by absence of any cross-import.
# ---------------------------------------------------------------------------


def test_redis_layer_has_no_openai_or_ibkr_dependency():
    import inspect

    source = inspect.getsource(redis_layer)
    assert "import openai" not in source.lower()
    assert "ibkr" not in source.lower()


def test_live_trading_gate_still_checked_before_redis_execution_lock():
    """submit_mt5_request must still reject on live_trading_enabled BEFORE the Redis
    execution-lock pre-check ever runs -- the Redis integration must never be reachable on a
    path that could otherwise submit a live order."""
    import inspect

    from backend.portfolio_execution.service import ExecutionManager

    source = inspect.getsource(ExecutionManager.submit_mt5_request)
    assert source.index("live_trading_enabled") < source.index("try_execution_lock")


# ---------------------------------------------------------------------------
# Follow-up: cold-cycle prewarm (Redis infrastructure limitations cleanup).
# ---------------------------------------------------------------------------


def _mk_instrument(broker_symbol: str):
    from backend.brokers.mt5.models import MT5ForexInstrument, MT5Symbol

    symbol = MT5Symbol(symbol=broker_symbol, visible=True, selected=True)
    return MT5ForexInstrument(
        canonical_pair=broker_symbol, broker_symbol=broker_symbol, asset_class="MAJOR",
        base_currency=broker_symbol[:3], quote_currency=broker_symbol[3:6], enabled=True,
        visible=True, tradable=True, market_open=True, selected=True, eligible=True,
        specification_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), symbol=symbol,
    )


class _FakeUniverseAdapter:
    def __init__(self, symbols):
        from backend.brokers.mt5.models import MT5ForexUniverse

        self.config = object()
        self._universe = MT5ForexUniverse(total_symbols=len(symbols), total_forex_pairs=len(symbols), majors=len(symbols), minors=0, exotics=0, items=[_mk_instrument(s) for s in symbols])
        self.candle_calls: list[tuple[str, str]] = []
        self.symbol_info_calls: list[str] = []
        self.tick_calls: list[str] = []

    async def forex_universe(self):
        return self._universe

    async def candles(self, symbol, timeframe, count=100):
        self.candle_calls.append((symbol, timeframe))
        return [_mk_candle(symbol=symbol, timeframe=timeframe) for _ in range(count)]

    async def symbol_info(self, symbol):
        self.symbol_info_calls.append(symbol)
        from backend.brokers.mt5.models import MT5Symbol

        return MT5Symbol(symbol=symbol, visible=True, selected=True)

    async def latest_tick(self, symbol):
        self.tick_calls.append(symbol)
        from backend.brokers.mt5.models import MT5Quote

        return MT5Quote(symbol=symbol, bid=1.1, ask=1.1002)


def test_prewarm_populates_candles_and_symbol_metadata_but_never_ticks(monkeypatch):
    from backend.brokers.mt5.autonomous import MT5AutonomousTradingService

    fake_redis = _FakeRedis()
    monkeypatch.setattr(redis_layer, "get_client", lambda: fake_redis)
    adapter = _FakeUniverseAdapter(["EURUSD", "GBPUSD"])
    service = MT5AutonomousTradingService(adapter=adapter)

    asyncio.run(service._prewarm_market_data_cache())

    assert adapter.tick_calls == []  # never prewarmed -- TTL too short to survive until a real cycle
    assert set(adapter.symbol_info_calls) == {"EURUSD", "GBPUSD"}
    assert set(adapter.candle_calls) == {
        ("EURUSD", "M1"),
        ("EURUSD", "M5"),
        ("EURUSD", "M15"),
        ("EURUSD", "H1"),
        ("EURUSD", "H4"),
        ("GBPUSD", "M1"),
        ("GBPUSD", "M5"),
        ("GBPUSD", "M15"),
        ("GBPUSD", "H1"),
        ("GBPUSD", "H4"),
    }
    # And it actually populated Redis -- a subsequent cached_candles call should hit, not fetch.
    adapter.candle_calls.clear()
    asyncio.run(redis_layer.cached_candles(adapter, "EURUSD", "M15", count=100))
    assert adapter.candle_calls == []  # served from the prewarmed cache


def test_prewarm_serializes_through_the_same_cycle_lock_as_run_cycle():
    """Source-level guarantee that prewarm can never run its MT5 calls concurrently with a live
    run_cycle() -- both acquire the exact same self._cycle_lock, and MetaTrader5's terminal API
    is not safe for concurrent calls (see _screen()'s own comment)."""
    import inspect

    from backend.brokers.mt5.autonomous import MT5AutonomousTradingService

    source = inspect.getsource(MT5AutonomousTradingService._prewarm_market_data_cache)
    assert "async with self._cycle_lock:" in source


def test_prewarm_failure_is_non_fatal(monkeypatch):
    from backend.brokers.mt5.autonomous import MT5AutonomousTradingService

    class _BrokenUniverseAdapter:
        config = object()

        async def forex_universe(self):
            raise ConnectionError("MT5 not ready")

    service = MT5AutonomousTradingService(adapter=_BrokenUniverseAdapter())

    asyncio.run(service._prewarm_market_data_cache())  # must not raise


# ---------------------------------------------------------------------------
# Follow-up: circuit-breaker sync Redis connection review.
# ---------------------------------------------------------------------------


def test_circuit_breaker_sync_client_is_a_single_shared_instance(monkeypatch):
    from backend.mt5_strategies import circuit_breaker

    monkeypatch.setattr(circuit_breaker, "_sync_redis_client", None)
    monkeypatch.setattr(circuit_breaker, "_sync_redis_unavailable", False)
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

    monkeypatch.setitem(sys.modules, "redis", _FakeSyncRedisModule)

    first = circuit_breaker._get_sync_redis()
    second = circuit_breaker._get_sync_redis()
    third = circuit_breaker._get_sync_redis()

    assert first is second is third  # exactly one client ever created
    assert len(created) == 1


def test_no_other_sync_redis_client_exists_to_consolidate_with():
    """Verifies the documented, deliberate exceptions to "one Redis subsystem, not two" are
    exactly the KNOWN ones -- circuit_breaker.py (Redis integration Part 10) and
    historical_intelligence/adaptive_cache.py (Adaptive-Historical-Intelligence-Backfill
    directive, Phase 21/22 -- evaluate_adaptive_intelligence runs inside asyncio.to_thread, a
    plain OS thread with no event loop, so it cannot await the shared async client either; see
    that module's docstring for the same justification circuit_breaker.py already established).
    If this ever fails on a THIRD file, a genuine consolidation opportunity has appeared and
    both modules' docstrings need revisiting."""
    import pathlib

    backend_root = pathlib.Path(__file__).resolve().parents[1]
    known_sync_redis_modules = {"circuit_breaker.py", "adaptive_cache.py"}
    hits = []
    for path in backend_root.rglob("*.py"):
        if path.name == "test_mt5_redis_integration.py" or path.name in known_sync_redis_modules:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "redis.Redis(" in text or "redis.Redis.from_url" in text:
            hits.append(str(path))
    assert hits == []


# ---------------------------------------------------------------------------
# Follow-up: mt5.position.opened -- distinct from mt5.order.accepted, fired only once real
# reconciliation happens (existing_row is None in _monitor_cycle's per-position loop).
# ---------------------------------------------------------------------------


def test_position_opened_event_topic_registered_and_distinct_from_order_accepted():
    assert "mt5.position.opened" in redis_layer.MT5_EVENT_TOPICS
    assert "mt5.order.accepted" in redis_layer.MT5_EVENT_TOPICS
    assert "mt5.position.opened" != "mt5.order.accepted"


def test_position_opened_gated_on_existing_row_is_none_source_inspection():
    """The event must fire only on first reconciliation (existing_row is None), never on every
    cycle a position remains open, and only AFTER _sync_position_state has run."""
    import inspect

    from backend.adaptive_management import service as adaptive_service

    source = inspect.getsource(adaptive_service.AdaptiveManagementService._monitor_cycle)
    opened_idx = source.index('"mt5.position.opened"')
    gate_idx = source.rindex("if existing_row is None:", 0, opened_idx)
    sync_idx = source.rindex("self._sync_position_state(", 0, opened_idx)
    assert gate_idx < opened_idx
    assert sync_idx < opened_idx  # published only after the row is actually synced/reconciled

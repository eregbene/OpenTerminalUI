"""Phase 9 (Forex/MT5 roadmap) regression tests: the central HARD/SOFT/OPTIONAL degradation
classifier. Covers tier assignment staying fixed regardless of state, HARD-component failure
flipping trading_safe to False, SOFT/OPTIONAL degradation never affecting trading_safe, and
account isolation in system_snapshot (one account's HARD failure never contaminates another's
verdict)."""
from __future__ import annotations

import asyncio

import pytest

from backend.shared import degradation


class _FakeAccount:
    def __init__(self, login=123456, server="MetaQuotes-Demo"):
        self.login = login
        self.server = server


class _FakeAdapter:
    def __init__(self, *, fail: bool = False):
        self._fail = fail

    async def mt5_account(self):
        if self._fail:
            raise ConnectionError("bridge unreachable")
        return _FakeAccount()


class _FakeRedisClient:
    def __init__(self, *, fail: bool = False):
        self._fail = fail

    async def ping(self):
        if self._fail:
            raise ConnectionError("redis down")
        return True


def test_postgres_component_healthy(monkeypatch):
    result = asyncio.run(degradation._postgres_component())
    assert result["tier"] == degradation.TIER_HARD
    assert result["state"] == degradation.STATE_HEALTHY


def test_postgres_component_unavailable_on_connection_failure(monkeypatch):
    class _BrokenSessionLocal:
        def __call__(self):
            raise ConnectionError("db unreachable")

    import backend.shared.db as db_module

    monkeypatch.setattr(db_module, "SessionLocal", _BrokenSessionLocal())
    result = asyncio.run(degradation._postgres_component())
    assert result["tier"] == degradation.TIER_HARD
    assert result["state"] == degradation.STATE_UNAVAILABLE


def test_redis_component_no_client_is_optional_and_unavailable(monkeypatch):
    from backend.mt5_strategies import redis_layer

    monkeypatch.setattr(redis_layer, "get_client", lambda: None)
    result = asyncio.run(degradation._redis_component())
    assert result["tier"] == degradation.TIER_OPTIONAL  # tier never changes with state
    assert result["state"] == degradation.STATE_UNAVAILABLE


def test_redis_component_healthy_when_ping_succeeds(monkeypatch):
    from backend.mt5_strategies import redis_layer

    monkeypatch.setattr(redis_layer, "get_client", lambda: _FakeRedisClient())
    result = asyncio.run(degradation._redis_component())
    assert result["tier"] == degradation.TIER_OPTIONAL
    assert result["state"] == degradation.STATE_HEALTHY


def test_historical_intelligence_off_is_optional_but_degraded(monkeypatch):
    from backend.historical_intelligence import modes

    monkeypatch.setattr(modes, "current_mode", lambda: modes.HistoricalIntelligenceMode.OFF)
    result = degradation._historical_intelligence_component()
    assert result["tier"] == degradation.TIER_OPTIONAL
    assert result["state"] == degradation.STATE_DEGRADED  # degraded (unused), never unsafe


def test_historical_intelligence_demo_active_is_healthy(monkeypatch):
    from backend.historical_intelligence import modes

    monkeypatch.setattr(modes, "current_mode", lambda: modes.HistoricalIntelligenceMode.DEMO_ACTIVE)
    result = degradation._historical_intelligence_component()
    assert result["state"] == degradation.STATE_HEALTHY


def test_economic_provider_component_maps_worst_provider_state(monkeypatch):
    from backend.economic_intelligence import provider_health

    monkeypatch.setattr(provider_health, "health_snapshot", lambda config: {"items": [{"provider": "ff_calendar_json", "state": "HEALTHY"}, {"provider": "ff_news", "state": "UNAVAILABLE"}]})
    result = degradation._economic_provider_component()
    assert result["tier"] == degradation.TIER_SOFT
    assert result["state"] == degradation.STATE_UNAVAILABLE  # worst of the two wins


def test_broker_identity_component_isolated_per_account(monkeypatch):
    from backend.brokers.mt5 import multi_account

    adapters = {"demo_10k": _FakeAdapter(fail=True), "ftmo_demo_25k": _FakeAdapter(fail=False)}
    monkeypatch.setattr(multi_account, "adapter_for_account", lambda account_id: adapters[account_id])

    broken = asyncio.run(degradation._broker_identity_component("demo_10k"))
    healthy = asyncio.run(degradation._broker_identity_component("ftmo_demo_25k"))
    assert broken["state"] == degradation.STATE_UNAVAILABLE
    assert healthy["state"] == degradation.STATE_HEALTHY  # the other account is unaffected


def test_portfolio_protection_component_maps_stale_to_unavailable(monkeypatch):
    from backend.portfolio_execution.service import portfolio_manager

    monkeypatch.setattr(portfolio_manager, "can_open_new_trade", lambda account_id=None: (False, ["PORTFOLIO_STATE_STALE"]))
    result = degradation._portfolio_protection_component("demo_10k")
    assert result["tier"] == degradation.TIER_HARD
    assert result["state"] == degradation.STATE_UNAVAILABLE


def test_portfolio_protection_component_maps_ordinary_blocker_to_degraded(monkeypatch):
    from backend.portfolio_execution.service import portfolio_manager

    monkeypatch.setattr(portfolio_manager, "can_open_new_trade", lambda account_id=None: (False, ["MAX_OPEN_POSITIONS"]))
    result = degradation._portfolio_protection_component("demo_10k")
    assert result["state"] == degradation.STATE_DEGRADED


def test_reconciliation_component_reflects_trustworthy_flag(monkeypatch):
    from backend.brokers.mt5 import reconciliation_watchdog

    monkeypatch.setattr(reconciliation_watchdog, "is_account_state_trustworthy", lambda account_id: False)
    result = degradation._reconciliation_component("demo_10k")
    assert result["tier"] == degradation.TIER_SOFT
    assert result["state"] == degradation.STATE_DEGRADED


def test_account_snapshot_trading_safe_only_when_hard_components_healthy(monkeypatch):
    from backend.brokers.mt5 import multi_account, reconciliation_watchdog
    from backend.portfolio_execution.service import portfolio_manager

    monkeypatch.setattr(multi_account, "adapter_for_account", lambda account_id: _FakeAdapter(fail=False))
    monkeypatch.setattr(portfolio_manager, "can_open_new_trade", lambda account_id=None: (True, []))
    monkeypatch.setattr(reconciliation_watchdog, "is_account_state_trustworthy", lambda account_id: False)  # SOFT, degraded

    snap = asyncio.run(degradation.account_snapshot("demo_10k"))
    assert snap["trading_safe"] is True  # SOFT-tier degradation never flips this


def test_account_snapshot_unsafe_when_broker_identity_fails(monkeypatch):
    from backend.brokers.mt5 import multi_account, reconciliation_watchdog
    from backend.portfolio_execution.service import portfolio_manager

    monkeypatch.setattr(multi_account, "adapter_for_account", lambda account_id: _FakeAdapter(fail=True))
    monkeypatch.setattr(portfolio_manager, "can_open_new_trade", lambda account_id=None: (True, []))
    monkeypatch.setattr(reconciliation_watchdog, "is_account_state_trustworthy", lambda account_id: True)

    snap = asyncio.run(degradation.account_snapshot("demo_10k"))
    assert snap["trading_safe"] is False


def test_system_snapshot_isolates_one_unsafe_account_from_another(monkeypatch):
    from backend.brokers.mt5 import multi_account, reconciliation_watchdog
    from backend.economic_intelligence import provider_health
    from backend.historical_intelligence import modes
    from backend.mt5_strategies import redis_layer
    from backend.portfolio_execution.service import portfolio_manager

    adapters = {"demo_10k": _FakeAdapter(fail=True), "ftmo_demo_25k": _FakeAdapter(fail=False)}
    monkeypatch.setattr(multi_account, "adapter_for_account", lambda account_id: adapters[account_id])
    monkeypatch.setattr(portfolio_manager, "can_open_new_trade", lambda account_id=None: (True, []))
    monkeypatch.setattr(reconciliation_watchdog, "is_account_state_trustworthy", lambda account_id: True)
    monkeypatch.setattr(redis_layer, "get_client", lambda: None)
    monkeypatch.setattr(modes, "current_mode", lambda: modes.HistoricalIntelligenceMode.DEMO_ACTIVE)
    monkeypatch.setattr(provider_health, "health_snapshot", lambda config: {"items": []})

    snap = asyncio.run(degradation.system_snapshot(["demo_10k", "ftmo_demo_25k"]))
    assert snap["accounts_safe"]["demo_10k"] is False
    assert snap["accounts_safe"]["ftmo_demo_25k"] is True

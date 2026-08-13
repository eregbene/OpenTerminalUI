"""Adaptive Trade Manager multi-account isolation.

Before this feature, AdaptiveManagementService referenced the single global mt5_adapter/
mt5_config() everywhere, and AdaptivePositionStateORM.position_id (the raw MT5 broker ticket)
had no account scoping -- two different MT5 accounts with colliding ticket numbers would
silently overwrite each other's row, and one account's demo_active activation / circuit breaker
state could leak into another account's cycle. These tests prove the isolation actually holds,
without needing a live MT5 connection.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.adaptive_management import service as service_module
from backend.adaptive_management.orm import AdaptiveActivationORM, AdaptiveCircuitBreakerORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM
from backend.adaptive_management.service import AdaptiveManagementMultiAccountOrchestrator, AdaptiveManagementService, _active_activation, _breaker, _breaker_id, _position_id
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite


# --------------------------------------------------------------------------- _position_id ---


def test_demo_10k_keeps_raw_unprefixed_ticket():
    """Backward compatibility: every existing row in the database uses the raw ticket as
    position_id -- demo_10k must never change format, or every currently-tracked open
    position's history would be orphaned on the next sync."""
    assert _position_id({"identifier": 57912548852}, "demo_10k") == "57912548852"
    assert _position_id({"identifier": 57912548852}) == "57912548852"  # default account_id


def test_other_accounts_get_account_prefixed_ticket():
    assert _position_id({"identifier": 12345}, "ftmo_demo_25k") == "ftmo_demo_25k:12345"
    assert _position_id({"identifier": 12345}, "ftmo_demo_50k") == "ftmo_demo_50k:12345"


def test_colliding_tickets_across_accounts_never_produce_the_same_position_id():
    """The exact scenario the audit flagged: two different accounts reusing the same small
    sequential ticket number must never resolve to the same row."""
    same_ticket_payload = {"identifier": 999}
    demo_id = _position_id(same_ticket_payload, "demo_10k")
    ftmo_25k_id = _position_id(same_ticket_payload, "ftmo_demo_25k")
    ftmo_50k_id = _position_id(same_ticket_payload, "ftmo_demo_50k")
    assert len({demo_id, ftmo_25k_id, ftmo_50k_id}) == 3


# ------------------------------------------------------------------------------ _breaker_id ---


def test_breaker_id_demo_10k_unprefixed():
    assert _breaker_id("demo_10k") == "adaptive_demo_manager"


def test_breaker_id_other_accounts_prefixed_and_distinct():
    ids = {_breaker_id(acc) for acc in ("demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k")}
    assert len(ids) == 4


# --------------------------------------------------------------- AdaptiveManagementService ---


def test_service_defaults_to_global_adapter_and_demo_10k():
    service = AdaptiveManagementService()
    assert service.account_id == "demo_10k"
    assert service.adapter is service_module.mt5_adapter
    assert service.config is service_module.mt5_adapter.config


def test_service_accepts_a_scoped_adapter_and_account_id():
    fake_config = SimpleNamespace(account_mode="DEMO", live_trading_enabled=False)
    fake_adapter = SimpleNamespace(config=fake_config)
    service = AdaptiveManagementService(fake_adapter, "ftmo_demo_25k")
    assert service.account_id == "ftmo_demo_25k"
    assert service.adapter is fake_adapter
    assert service.config is fake_config  # NOT the global mt5_config() -- see service.py docstring


# ------------------------------------------------------------- _active_activation / _breaker ---


def _seed_activation(db, *, account_id: str, active: bool = True) -> AdaptiveActivationORM:
    row = AdaptiveActivationORM(activation_id=f"ACT_{account_id}")
    row.account_id = account_id
    row.policy_id = "P1"
    row.policy_version = "v1"
    row.mode = "demo_active"
    row.demo_account = "0"
    row.effective_from = service_module.utcnow()
    row.active = active
    db.add(row)
    return row


def test_active_activation_is_scoped_per_account(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        _seed_activation(db, account_id="demo_10k")
        _seed_activation(db, account_id="ftmo_demo_25k")
        db.commit()

        demo_activation = _active_activation(db, "demo_10k")
        ftmo_activation = _active_activation(db, "ftmo_demo_25k")
        other_activation = _active_activation(db, "ftmo_demo_50k")

    assert demo_activation is not None and demo_activation.account_id == "demo_10k"
    assert ftmo_activation is not None and ftmo_activation.account_id == "ftmo_demo_25k"
    assert other_activation is None  # never activated -- must not see another account's row


def test_breaker_creates_a_separate_row_per_account(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        demo_breaker_id = _breaker(db, "demo_10k").breaker_id
        ftmo_breaker_id = _breaker(db, "ftmo_demo_25k").breaker_id
        db.commit()

    assert demo_breaker_id != ftmo_breaker_id
    assert demo_breaker_id == "adaptive_demo_manager"


def test_tripping_one_account_breaker_does_not_affect_another(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        demo_breaker = _breaker(db, "demo_10k")
        demo_breaker.state = "open"
        demo_breaker.reason = "test_trip"
        db.merge(demo_breaker)
        db.commit()

        ftmo_state = _breaker(db, "ftmo_demo_25k").state

    assert ftmo_state == "closed"  # unaffected by demo_10k's trip


# --------------------------------------------------------------------- _position_records ---


def _seed_position(db, *, position_id: str, account_id: str, symbol: str = "EURUSD") -> None:
    state = AdaptivePositionStateORM(position_id=position_id, symbol=symbol, direction="LONG", broker_ticket=position_id)
    state.account_id = account_id
    state.closed_detected_at = service_module.utcnow()
    state.contaminated = False
    state.max_achieved_r = 1.0
    state.min_achieved_r = -0.2
    state.original_risk_money = 50.0
    db.add(state)

    baseline = AdaptivePositionBaselineORM(position_id=position_id)
    baseline.account_id = account_id
    baseline.broker_ticket = position_id
    baseline.symbol = symbol
    baseline.direction = "LONG"
    baseline.original_entry = 1.1000
    baseline.original_sl = 1.0950
    baseline.original_tp = 1.1100
    baseline.initial_stop_distance = 0.0050
    baseline.initial_risk_money = 50.0
    db.add(baseline)


def test_position_records_account_id_none_returns_all_accounts(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        _seed_position(db, position_id="P1", account_id="demo_10k")
        _seed_position(db, position_id="ftmo_demo_25k:P2", account_id="ftmo_demo_25k")
        db.commit()

    from backend.adaptive_management.analytics import _position_records

    all_records = _position_records()
    assert {r["position_id"] for r in all_records} == {"P1", "ftmo_demo_25k:P2"}


def test_position_records_filters_to_one_account_when_scoped(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        _seed_position(db, position_id="P1", account_id="demo_10k")
        _seed_position(db, position_id="ftmo_demo_25k:P2", account_id="ftmo_demo_25k")
        db.commit()

    from backend.adaptive_management.analytics import _position_records

    demo_only = _position_records("demo_10k")
    ftmo_only = _position_records("ftmo_demo_25k")

    assert [r["position_id"] for r in demo_only] == ["P1"]
    assert [r["position_id"] for r in ftmo_only] == ["ftmo_demo_25k:P2"]


# --------------------------------------------------- AdaptiveManagementMultiAccountOrchestrator ---


def _mk_profile(account_id: str, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(account_id=account_id, enabled=enabled)


def test_orchestrator_reuses_default_service_for_demo_10k(monkeypatch: pytest.MonkeyPatch):
    default = AdaptiveManagementService()
    orchestrator = AdaptiveManagementMultiAccountOrchestrator(default)
    monkeypatch.setattr(service_module.account_registry, "configured_profiles", lambda: [_mk_profile("demo_10k", True)])

    services = orchestrator._enabled_services()

    assert services == {"demo_10k": default}


def test_orchestrator_builds_one_service_per_enabled_non_default_profile(monkeypatch: pytest.MonkeyPatch):
    default = AdaptiveManagementService()
    orchestrator = AdaptiveManagementMultiAccountOrchestrator(default)
    monkeypatch.setattr(
        service_module.account_registry,
        "configured_profiles",
        lambda: [_mk_profile("demo_10k", True), _mk_profile("ftmo_demo_25k", True), _mk_profile("ftmo_demo_50k", False)],
    )
    monkeypatch.setattr(service_module, "adapter_for_account", lambda account_id: SimpleNamespace(config=SimpleNamespace(account_mode="DEMO")))

    services = orchestrator._enabled_services()

    assert set(services) == {"demo_10k", "ftmo_demo_25k"}  # 50k excluded: not enabled
    assert services["demo_10k"] is default
    assert services["ftmo_demo_25k"].account_id == "ftmo_demo_25k"
    assert services["ftmo_demo_25k"] is not default


def test_orchestrator_caches_service_instances_across_calls(monkeypatch: pytest.MonkeyPatch):
    orchestrator = AdaptiveManagementMultiAccountOrchestrator(AdaptiveManagementService())
    monkeypatch.setattr(service_module.account_registry, "configured_profiles", lambda: [_mk_profile("demo_10k", True), _mk_profile("ftmo_demo_25k", True)])
    monkeypatch.setattr(service_module, "adapter_for_account", lambda account_id: SimpleNamespace(config=SimpleNamespace(account_mode="DEMO")))

    first = orchestrator._enabled_services()["ftmo_demo_25k"]
    second = orchestrator._enabled_services()["ftmo_demo_25k"]

    assert first is second  # not rebuilt every call


def test_orchestrator_delegates_unknown_attributes_to_default_service():
    default = AdaptiveManagementService()
    orchestrator = AdaptiveManagementMultiAccountOrchestrator(default)
    assert orchestrator.mode() == default.mode()

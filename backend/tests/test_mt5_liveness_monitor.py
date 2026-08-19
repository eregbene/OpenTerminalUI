"""2026-08-19: real incident -- the MT5 multi-account scheduler silently stopped completing
cycles for ~3 hours (a Docker Desktop engine hang), with no crash and no error log, undetected
until a user noticed no trades were happening. MT5LivenessMonitor watches the same in-memory
last_cycle_time timestamp the scheduler updates on every completed cycle and alerts (log +
in-app notification) the moment it goes stale, instead of hours later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.brokers.mt5.liveness import MT5LivenessMonitor
from backend.models.notification import Notification
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite


def _set_last_cycle_time(value) -> None:
    mt5_autonomous_service.state.last_cycle_time = value


def test_never_alerts_before_first_cycle_completes(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _set_last_cycle_time(None)
    monitor = MT5LivenessMonitor()

    result = monitor.check_once()

    assert result is None


def test_never_alerts_when_cycle_is_recent(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=2))
    monitor = MT5LivenessMonitor()

    result = monitor.check_once()

    assert result is None


def test_alerts_when_cycle_is_stale(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_LIVENESS_STALE_THRESHOLD_SECONDS", "900")
    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=20))
    monitor = MT5LivenessMonitor()

    result = monitor.check_once()

    assert result is not None
    assert result["age_minutes"] >= 20
    with SessionLocal() as db:
        row = db.query(Notification).filter(Notification.type == "system", Notification.priority == "critical").order_by(Notification.created_at.desc()).first()
        assert row is not None
        assert "scheduler stalled" in row.title.lower() or "stalled" in row.title.lower()


def test_does_not_re_alert_for_the_same_stall(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    _set_last_cycle_time(stale_time)
    monitor = MT5LivenessMonitor()

    first = monitor.check_once()
    second = monitor.check_once()

    assert first is not None
    assert second is None
    with SessionLocal() as db:
        count = db.query(Notification).filter(Notification.type == "system", Notification.priority == "critical").count()
        assert count == 1


def test_re_alerts_after_recovery_and_a_new_stall(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monitor = MT5LivenessMonitor()

    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=20))
    first = monitor.check_once()
    assert first is not None

    # cycles resume
    _set_last_cycle_time(datetime.now(timezone.utc))
    healthy = monitor.check_once()
    assert healthy is None

    # a NEW stall, different last_cycle_time
    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=25))
    second = monitor.check_once()

    assert second is not None
    with SessionLocal() as db:
        count = db.query(Notification).filter(Notification.type == "system", Notification.priority == "critical").count()
        assert count == 2


def test_configurable_threshold(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_LIVENESS_STALE_THRESHOLD_SECONDS", "60")
    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=2))
    monitor = MT5LivenessMonitor()

    result = monitor.check_once()

    assert result is not None


def test_naive_last_cycle_time_does_not_crash(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _set_last_cycle_time(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20))
    monitor = MT5LivenessMonitor()

    result = monitor.check_once()

    assert result is not None


def test_notification_failure_does_not_raise(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _set_last_cycle_time(datetime.now(timezone.utc) - timedelta(minutes=20))
    monitor = MT5LivenessMonitor()

    import backend.api.routes.notifications as notifications_module

    def _boom(**kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(notifications_module, "create_notification", _boom)

    result = monitor.check_once()

    assert result is not None  # the alert itself (log + return value) still fires despite the in-app notification failing

"""2026-08-18: max_trades_per_day/max_trades_per_symbol_per_day/post_trade_cooldown_minutes
(config.py:73-78) were declared but never enforced anywhere in the MT5 autonomous path --
entries_submitted_today was incremented in _submit() but never compared against anything, and
the underlying DB-backed state store had no day-boundary reset either (confirmed live: 65-85 real
entries on single days against a nominal cap of 20). Found while verifying an external forensic
report's claims against the real code before implementing its top-priority recommendation.
Mirrors backend.intelligence.trading.auto_paper.py's own, already-working enforcement for the
identical fields.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def _service() -> MT5AutonomousTradingService:
    return MT5AutonomousTradingService(fake_adapter())


def test_daily_trade_state_starts_at_zero(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    state = service._daily_trade_state()
    assert state["entries_submitted_today"] == 0
    assert state["entries_by_symbol"] == {}


def test_daily_trade_state_resets_on_new_day(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._stored()
    store["trade_cap_date"] = "2020-01-01"  # a stale, long-past date
    store["entries_submitted_today"] = 999
    store["entries_by_symbol"] = {"EURUSD": 999}
    service._set_stored(store)

    state = service._daily_trade_state()

    assert state["entries_submitted_today"] == 0
    assert state["entries_by_symbol"] == {}
    assert state["trade_cap_date"] == datetime.now(timezone.utc).date().isoformat()


def test_daily_trade_state_preserves_same_day_counts(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._daily_trade_state()
    store["entries_submitted_today"] = 5
    service._set_stored(store)

    state = service._daily_trade_state()

    assert state["entries_submitted_today"] == 5


def test_trade_frequency_blockers_empty_when_under_all_caps(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    assert service._trade_frequency_blockers({"broker_symbol": "EURUSD"}) == []


def test_trade_frequency_blockers_daily_limit(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._daily_trade_state()
    store["entries_submitted_today"] = service.config.max_trades_per_day
    service._set_stored(store)

    blockers = service._trade_frequency_blockers({"broker_symbol": "EURUSD"})

    assert "DAILY_TRADE_LIMIT" in blockers


def test_trade_frequency_blockers_symbol_limit(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._daily_trade_state()
    store["entries_by_symbol"] = {"EURUSD": service.config.max_trades_per_symbol_per_day}
    service._set_stored(store)

    blockers = service._trade_frequency_blockers({"broker_symbol": "EURUSD"})
    other_symbol_blockers = service._trade_frequency_blockers({"broker_symbol": "GBPUSD"})

    assert "SYMBOL_DAILY_TRADE_LIMIT" in blockers
    assert "SYMBOL_DAILY_TRADE_LIMIT" not in other_symbol_blockers


def test_trade_frequency_blockers_cooldown(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._daily_trade_state()
    store["last_entry_at_by_symbol"] = {"EURUSD": datetime.now(timezone.utc).isoformat()}
    service._set_stored(store)

    blockers = service._trade_frequency_blockers({"broker_symbol": "EURUSD"})

    assert "COOLDOWN" in blockers


def test_trade_frequency_blockers_cooldown_expires(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    store = service._daily_trade_state()
    expired = datetime.now(timezone.utc) - timedelta(minutes=service.config.post_trade_cooldown_minutes + 1)
    store["last_entry_at_by_symbol"] = {"EURUSD": expired.isoformat()}
    service._set_stored(store)

    blockers = service._trade_frequency_blockers({"broker_symbol": "EURUSD"})

    assert "COOLDOWN" not in blockers


def test_daily_trade_state_survives_realistic_volume_without_blocking_reasonable_days(monkeypatch: pytest.MonkeyPatch):
    """Regression proof for the exact bug found: 20 real submissions in one day must correctly
    hit the cap (this used to never happen at all -- the counter was incremented but nothing
    ever compared against it)."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = _service()
    for _ in range(service.config.max_trades_per_day - 1):
        assert service._trade_frequency_blockers({"broker_symbol": "EURUSD"}) == []
        store = service._daily_trade_state()
        store["entries_submitted_today"] = int(store.get("entries_submitted_today") or 0) + 1
        service._set_stored(store)
    # One more submission reaches the cap exactly.
    store = service._daily_trade_state()
    store["entries_submitted_today"] = int(store.get("entries_submitted_today") or 0) + 1
    service._set_stored(store)
    assert "DAILY_TRADE_LIMIT" in service._trade_frequency_blockers({"broker_symbol": "EURUSD"})

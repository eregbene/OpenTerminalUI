"""2026-08-19: companion to test_mt5_losing_streak_gate.py, same real incident. That gate only
fires after several unanimous adverse CLOSES accumulate; the actual gold cluster that night was
near-simultaneous instead -- ftmo_demo_100k and ftmo_demo_25k both went SHORT XAUUSD 18 minutes
apart, independently, before either had a losing trade on record. Nothing stopped 4 independently
-run accounts from turning one directional read into several copies of the same bet.

Tests _cross_account_concurrent_exposure_blockers directly (pure portfolio-snapshot read +
decision logic, no adapter/broker calls) by monkeypatching portfolio_manager.latest_snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.brokers.mt5.autonomous as autonomous_module
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.tests.test_mt5_adapter import fake_adapter


def _other_account_ids(exclude: str, count: int) -> list[str]:
    ids = [p.account_id for p in account_registry.configured_profiles() if p.enabled and p.account_id != exclude]
    assert len(ids) >= count, f"test requires at least {count} other enabled MT5 accounts, found {ids}"
    return ids[:count]


def _snapshot(*, positions: list[dict], age_seconds: float = 5.0) -> dict:
    created_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"created_at": created_at, "raw_payload": {"positions": positions}}


def _position(symbol: str, direction: str) -> dict:
    return {"symbol": symbol, "type": 0 if direction == "LONG" else 1}


def _patch_snapshots(monkeypatch, snapshots: dict) -> None:
    def fake_latest_snapshot(account_id=None):
        if account_id in snapshots:
            value = snapshots[account_id]
            if isinstance(value, Exception):
                raise value
            return value
        return None

    monkeypatch.setattr(autonomous_module.portfolio_manager, "latest_snapshot", fake_latest_snapshot)


def _service() -> MT5AutonomousTradingService:
    return MT5AutonomousTradingService(fake_adapter())


def _candidate(symbol: str = "XAUUSD", direction: str = "SHORT") -> dict:
    return {"broker_symbol": symbol, "direction": direction}


def test_blocks_when_threshold_reached(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("XAUUSD", "SHORT")]),
        b: _snapshot(positions=[_position("XAUUSD", "SHORT")]),
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert "CROSS_ACCOUNT_CONCURRENT_EXPOSURE" in blockers


def test_allows_when_below_threshold(monkeypatch):
    svc = _service()
    (a,) = _other_account_ids(svc.account_id, 1)
    _patch_snapshots(monkeypatch, {a: _snapshot(positions=[_position("XAUUSD", "SHORT")])})

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_opposite_direction_not_counted(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("XAUUSD", "LONG")]),
        b: _snapshot(positions=[_position("XAUUSD", "LONG")]),
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate("XAUUSD", "SHORT"))

    assert blockers == []


def test_different_symbol_not_counted(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("EURUSD", "SHORT")]),
        b: _snapshot(positions=[_position("EURUSD", "SHORT")]),
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate("XAUUSD", "SHORT"))

    assert blockers == []


def test_stale_snapshot_excluded_fails_open(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("XAUUSD", "SHORT")], age_seconds=5.0),
        b: _snapshot(positions=[_position("XAUUSD", "SHORT")], age_seconds=999.0),  # older than 180s default
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_missing_snapshot_excluded(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {a: _snapshot(positions=[_position("XAUUSD", "SHORT")])})
    # b has no entry at all -> latest_snapshot returns None

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_snapshot_read_error_excludes_that_account(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("XAUUSD", "SHORT")]),
        b: RuntimeError("db unavailable"),
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_kill_switch_disables_gate(monkeypatch):
    monkeypatch.setenv("MT5_CONCURRENT_EXPOSURE_CONFIRMATION_REQUIRED", "false")
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {
        a: _snapshot(positions=[_position("XAUUSD", "SHORT")]),
        b: _snapshot(positions=[_position("XAUUSD", "SHORT")]),
    })

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_configurable_threshold(monkeypatch):
    monkeypatch.setenv("MT5_CONCURRENT_EXPOSURE_MAX_ACCOUNTS", "1")
    svc = _service()
    (a,) = _other_account_ids(svc.account_id, 1)
    _patch_snapshots(monkeypatch, {a: _snapshot(positions=[_position("XAUUSD", "SHORT")])})

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert "CROSS_ACCOUNT_CONCURRENT_EXPOSURE" in blockers


def test_no_open_positions_never_blocks(monkeypatch):
    svc = _service()
    a, b = _other_account_ids(svc.account_id, 2)
    _patch_snapshots(monkeypatch, {a: _snapshot(positions=[]), b: _snapshot(positions=[])})

    blockers = svc._cross_account_concurrent_exposure_blockers(_candidate())

    assert blockers == []


def test_missing_symbol_or_no_trade_direction_never_blocks(monkeypatch):
    svc = _service()
    assert svc._cross_account_concurrent_exposure_blockers({"broker_symbol": None, "direction": "SHORT"}) == []
    assert svc._cross_account_concurrent_exposure_blockers({"broker_symbol": "XAUUSD", "direction": "NO_TRADE"}) == []

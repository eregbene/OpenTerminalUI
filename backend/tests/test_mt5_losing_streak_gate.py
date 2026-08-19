"""2026-08-19: real incident -- three independent strategies (ema_trend/smc_continuation/mtfai1)
each shorted XAUUSD overnight, across multiple accounts, while gold ground higher through a
sustained (choppy but net-bullish) move; each got invalidated/critical in turn and a fresh SHORT
was re-entered anyway. No existing gate looked at repeated same-direction losses on a symbol --
trade-frequency limits cap volume, not direction.

Tests _symbol_direction_losing_streak_blockers directly (pure DB-query + decision logic, no
adapter/broker calls) using the isolated-sqlite pattern from test_mt5_trade_frequency_cap.py.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter

_counter = itertools.count()


def _service() -> MT5AutonomousTradingService:
    return MT5AutonomousTradingService(fake_adapter())


def _insert_closed(*, account_id: str, symbol: str, direction: str, exit_reason: str, created_at: datetime, strategy: str = "ema_trend") -> None:
    n = next(_counter)
    with SessionLocal() as db:
        db.add(
            MT5CandidateEvaluationORM(
                evaluation_id=f"EVAL_{n}",
                cycle_id=f"CYCLE_{n}",
                account_id=account_id,
                candidate_id=f"CAND_{n}",
                created_at=created_at,
                symbol=symbol,
                broker_symbol=symbol,
                direction=direction,
                strategy=strategy,
                overall_confidence=80.0,
                confidence_band="HIGH",
                selected=True,
                eligible_for_execution=True,
                outcome_type="EXECUTED",
                outcome_status="CLOSED",
                exit_reason=exit_reason,
            )
        )
        db.commit()


def _candidate(symbol: str = "XAUUSD", direction: str = "SHORT") -> dict:
    return {"broker_symbol": symbol, "direction": direction}


def test_three_consecutive_adverse_exits_blocks_new_entry(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="critical", created_at=now - timedelta(minutes=30), strategy="ema_trend")
    _insert_closed(account_id="ftmo_demo_100k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=20), strategy="smc_continuation")
    _insert_closed(account_id="ftmo_demo_25k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=10), strategy="mtfai1")

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert "SYMBOL_DIRECTION_LOSING_STREAK" in blockers


def test_streak_spans_multiple_accounts_not_just_one(monkeypatch):
    """The real incident hit several accounts making the identical directional call
    independently -- the gate must look across all accounts, not just the one about to trade."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    for i, account in enumerate(["ftmo_demo_50k", "ftmo_demo_100k", "demo_10k"]):
        _insert_closed(account_id=account, symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=30 - i * 10))

    blockers = _service()._symbol_direction_losing_streak_blockers({"broker_symbol": "XAUUSD", "direction": "SHORT"})

    assert "SYMBOL_DIRECTION_LOSING_STREAK" in blockers


def test_one_healthy_exit_breaks_the_streak(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="critical", created_at=now - timedelta(minutes=40))
    _insert_closed(account_id="ftmo_demo_100k", symbol="XAUUSD", direction="SHORT", exit_reason="weakening", created_at=now - timedelta(minutes=30))  # not adverse
    _insert_closed(account_id="ftmo_demo_25k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=20))

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert blockers == []


def test_opposite_direction_is_never_blocked(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    for i in range(3):
        _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=30 - i * 10))

    long_blockers = _service()._symbol_direction_losing_streak_blockers({"broker_symbol": "XAUUSD", "direction": "LONG"})

    assert long_blockers == []


def test_different_symbol_is_never_blocked(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    for i in range(3):
        _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=30 - i * 10))

    eurusd_blockers = _service()._symbol_direction_losing_streak_blockers({"broker_symbol": "EURUSD", "direction": "SHORT"})

    assert eurusd_blockers == []


def test_fewer_than_threshold_closed_trades_never_blocks(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=10))
    _insert_closed(account_id="ftmo_demo_100k", symbol="XAUUSD", direction="SHORT", exit_reason="critical", created_at=now - timedelta(minutes=5))

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert blockers == []


def test_cooldown_expires_after_configured_hours(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_LOSING_STREAK_COOLDOWN_HOURS", "2.0")
    old = datetime.now(timezone.utc) - timedelta(hours=3)  # older than the 2h cooldown
    for i in range(3):
        _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=old + timedelta(minutes=i * 5))

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert blockers == []


def test_kill_switch_disables_gate_entirely(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_LOSING_STREAK_CONFIRMATION_REQUIRED", "false")
    now = datetime.now(timezone.utc)
    for i in range(3):
        _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=30 - i * 10))

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert blockers == []


def test_configurable_threshold(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_LOSING_STREAK_THRESHOLD", "2")
    now = datetime.now(timezone.utc)
    _insert_closed(account_id="ftmo_demo_50k", symbol="XAUUSD", direction="SHORT", exit_reason="invalidated", created_at=now - timedelta(minutes=20))
    _insert_closed(account_id="ftmo_demo_100k", symbol="XAUUSD", direction="SHORT", exit_reason="critical", created_at=now - timedelta(minutes=10))

    blockers = _service()._symbol_direction_losing_streak_blockers(_candidate())

    assert "SYMBOL_DIRECTION_LOSING_STREAK" in blockers


def test_missing_symbol_or_no_trade_direction_never_blocks(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    assert _service()._symbol_direction_losing_streak_blockers({"broker_symbol": None, "direction": "SHORT"}) == []
    assert _service()._symbol_direction_losing_streak_blockers({"broker_symbol": "XAUUSD", "direction": "NO_TRADE"}) == []

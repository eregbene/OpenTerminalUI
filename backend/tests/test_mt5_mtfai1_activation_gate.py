"""2026-08-17: MTFAI1 was the only strategy in the multi-strategy layer with NO working
activation kill switch -- its row hardcoded strategy_activation="ACTIVE_MT5" regardless of
MT5_STRATEGY_ACTIVATION_MTFAI1, because its scoring is inline in autonomous.py, entirely outside
STRATEGY_FAMILIES/EVALUATORS (found while investigating why a real-money-adjacent DEMO account
kept losing on its highest-volume strategy). Fixed by wiring the row's strategy_activation field
to the same activation_status() every other family already uses. This is the regression test for
that specific fix -- reuses the same fake_adapter()/_screen() harness as
test_mt5_phase8_market_data_quality.py."""
from __future__ import annotations

import asyncio

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def _mtfai1_row(candidates):
    return next(c for c in candidates if c["context"]["strategy_id"] == "mtfai1")


def test_mtfai1_row_defaults_to_active_with_no_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_MTFAI1", raising=False)
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    universe = asyncio.run(adapter.forex_universe())
    eurusd = next(item for item in universe.items if item.broker_symbol == "EURUSD")

    candidates = asyncio.run(service._screen([eurusd], cycle_id="TEST_CYCLE"))
    row = _mtfai1_row(candidates)
    assert row["strategy_activation"] == "ACTIVE_MT5"


def test_mtfai1_row_reflects_shadow_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_MTFAI1", "SHADOW_MT5")
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    universe = asyncio.run(adapter.forex_universe())
    eurusd = next(item for item in universe.items if item.broker_symbol == "EURUSD")

    candidates = asyncio.run(service._screen([eurusd], cycle_id="TEST_CYCLE"))
    row = _mtfai1_row(candidates)
    assert row["strategy_activation"] == "SHADOW_MT5"


def test_mtfai1_row_reflects_disabled_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_MTFAI1", "DISABLED")
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    universe = asyncio.run(adapter.forex_universe())
    eurusd = next(item for item in universe.items if item.broker_symbol == "EURUSD")

    candidates = asyncio.run(service._screen([eurusd], cycle_id="TEST_CYCLE"))
    row = _mtfai1_row(candidates)
    assert row["strategy_activation"] == "DISABLED"

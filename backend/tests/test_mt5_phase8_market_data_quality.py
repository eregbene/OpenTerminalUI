"""Phase 8 (Forex/MT5 roadmap) regression test: market_data.candle_quality()'s OHLC-sanity
check (high<low, non-positive open/close) is now actually consulted by the live _screen()
eligibility path, not just computed by the unused eligible_candles() API. Reuses the same
fake_adapter()/FakeMT5 fixtures as test_mt5_adapter.py and test_mt5_autonomous_deterministic.py."""
from __future__ import annotations

import asyncio

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import FakeMT5, fake_adapter


class _BadOHLCMT5(FakeMT5):
    """Same as FakeMT5 except EURUSD's candle history includes one bar with high<low --
    an impossible OHLC value a length-only check would never catch."""

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        rows = super().copy_rates_from_pos(symbol, timeframe, start, count)
        if symbol == "EURUSD":
            rows[-1] = {**rows[-1], "high": 0.9, "low": 1.2}  # high < low: impossible
        return rows


def _bad_ohlc_adapter():
    adapter = fake_adapter()
    adapter.client._mt5 = _BadOHLCMT5()
    return adapter


def test_screen_rejects_candidate_with_impossible_ohlc(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = _bad_ohlc_adapter()
    service = MT5AutonomousTradingService(adapter)

    universe = asyncio.run(adapter.forex_universe())
    eurusd = next(item for item in universe.items if item.broker_symbol == "EURUSD")

    candidates = asyncio.run(service._screen([eurusd], cycle_id="TEST_CYCLE"))
    row = next(c for c in candidates if c["broker_symbol"] == "EURUSD")
    assert "DATA_QUALITY_FAILED" in row["rejection_reasons"]


def test_screen_accepts_clean_ohlc_history(monkeypatch: pytest.MonkeyPatch):
    """Control case: FakeMT5's normal (unmodified) candle history never trips
    DATA_QUALITY_FAILED -- proves the new check doesn't false-positive on healthy data."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)

    universe = asyncio.run(adapter.forex_universe())
    eurusd = next(item for item in universe.items if item.broker_symbol == "EURUSD")

    candidates = asyncio.run(service._screen([eurusd], cycle_id="TEST_CYCLE"))
    row = next(c for c in candidates if c["broker_symbol"] == "EURUSD")
    assert "DATA_QUALITY_FAILED" not in row["rejection_reasons"]

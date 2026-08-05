from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.models import MT5Symbol
from backend.intelligence.trading.market_context import MarketContext
from backend.intelligence.trading.strategies import EMATrendStrategy


def _context(**overrides) -> MarketContext:
    payload = dict(
        symbol="EURUSD",
        timeframe="M5",
        session="LONDON",
        timestamp=datetime.now(timezone.utc),
        spread=1.0,
        volatility=0.01,
        atr=0.0010,
        adr=0.005,
        liquidity_score=1.0,
        ema20=1.1010,
        ema50=1.1000,
        ema200=1.0990,
        ema20_slope=0.0002,
        ema50_slope=0.0001,
        ema200_slope=0.0001,
        higher_timeframe_trend="bullish",
        trend_strength=0.8,
        market_structure={},
        momentum={},
        volatility_state={},
        support_resistance={
            "current_price": 1.1020,
            "current_close": 1.1020,
            "current_high": 1.1025,
            "current_low": 1.1005,
            "nearest_support": 1.1000,
            "nearest_resistance": 1.1050,
        },
        sessions={},
        market_regime="TRENDING",
    )
    payload.update(overrides)
    return MarketContext(**payload)


def test_dynamic_stop_uses_structure_and_atr_not_flat_one_atr():
    output = EMATrendStrategy().evaluate(_context())

    assert output.decision == "LONG"
    assert output.valid is True
    old_flat_stop = 1.1020 - 0.0010
    assert output.stop != pytest.approx(old_flat_stop)
    assert output.stop < 1.1020
    assert output.stop == pytest.approx(1.0998, abs=1e-6)
    assert output.risk_reward == pytest.approx(1.8, rel=1e-6)


def test_dynamic_stop_rejects_trade_when_no_valid_stop_fits_bounds(monkeypatch):
    monkeypatch.setenv("STRATEGY_SL_MIN_ATR_MULT", "2.0")
    monkeypatch.setenv("STRATEGY_SL_MAX_ATR_MULT", "1.0")

    output = EMATrendStrategy().evaluate(_context())

    assert output.decision == "NO_TRADE"
    assert output.valid is False
    assert "NO_VALID_STOP_CONSTRUCTED" in output.rejection_codes


def test_dynamic_stop_respects_max_distance_bound(monkeypatch):
    monkeypatch.setenv("STRATEGY_SL_MAX_DISTANCE", "0.0015")

    output = EMATrendStrategy().evaluate(_context())

    assert output.decision == "LONG"
    assert abs(1.1020 - output.stop) <= 0.0015 + 1e-9


def _risk_service() -> MT5ExecutionService:
    return MT5ExecutionService(SimpleNamespace(config=MT5Config()))


def _symbol() -> MT5Symbol:
    return MT5Symbol(
        symbol="EURUSD",
        visible=True,
        selected=True,
        digits=5,
        point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"),
        trade_tick_value=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"),
        trade_tick_value_profit=Decimal("1.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def test_wider_stop_produces_smaller_risk_normalized_volume_same_monetary_risk():
    service = _risk_service()
    symbol = _symbol()
    entry = Decimal("1.10000")

    narrow = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("10000"), symbol=symbol, direction="LONG",
            entry=entry, stop=entry - Decimal("0.00100"), target=entry + Decimal("0.00300"),
        )
    )
    wide = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("10000"), symbol=symbol, direction="LONG",
            entry=entry, stop=entry - Decimal("0.00200"), target=entry + Decimal("0.00600"),
        )
    )

    assert narrow.status == "APPROVED"
    assert wide.status == "APPROVED"
    assert wide.volume < narrow.volume
    assert abs(float(wide.projected_loss_usd) - float(narrow.projected_loss_usd)) <= 2.0

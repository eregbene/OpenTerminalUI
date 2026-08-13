"""Phase 12 (Forex/MT5 roadmap) regression tests: DEMO vs tiny-live readiness checks.

Covers: ATR(14) pure computation (including the insufficient-history case), SKIP verdicts for
each failure mode (insufficient data, untrusted risk metadata, minimum lot exceeding the risk
budget), the READY verdict when the minimum lot fits the budget, and that this module NEVER
suggests a volume above the broker's own minimum (no "round up" path exists at all)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.mt5 import live_validation_readiness as readiness

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _candle(*, high: float, low: float, close: float, complete: bool = True) -> SimpleNamespace:
    return SimpleNamespace(high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)), complete=complete)


def _flat_candles(n: int, *, base: float = 1.1000, wick: float = 0.0010) -> list[SimpleNamespace]:
    return [_candle(high=base + wick, low=base - wick, close=base) for _ in range(n)]


# --- _atr14 (pure) -------------------------------------------------------------------------


def test_atr14_returns_none_with_insufficient_history():
    assert readiness._atr14(_flat_candles(10)) is None


def test_atr14_computes_positive_value_from_real_range():
    atr = readiness._atr14(_flat_candles(20, wick=0.0015))
    assert atr is not None
    assert atr > 0


def test_atr14_ignores_incomplete_bars():
    candles = _flat_candles(20) + [_candle(high=5.0, low=0.0, close=2.5, complete=False)]  # huge incomplete bar
    atr = readiness._atr14(candles)
    assert atr < Decimal("0.01")  # the huge incomplete bar must not blow up the average


# --- symbol_readiness: each verdict path ----------------------------------------------------


class _FakeAdapter:
    def __init__(self, *, candles=None, quote=None, raise_on_fetch=False):
        self._candles = candles
        self._quote = quote
        self._raise_on_fetch = raise_on_fetch

    async def candles(self, symbol, timeframe, count=30, completed_only=True):
        if self._raise_on_fetch:
            raise ConnectionError("bridge unreachable")
        return self._candles

    async def latest_tick(self, symbol):
        if self._raise_on_fetch:
            raise ConnectionError("bridge unreachable")
        return self._quote


def _instrument(volume_min: float = 0.01):
    symbol_info = SimpleNamespace(volume_min=volume_min)
    return SimpleNamespace(broker_symbol="EURUSD", canonical_pair="EURUSD", symbol=symbol_info)


def _quote(bid: float = 1.1000):
    return SimpleNamespace(bid=Decimal(str(bid)), ask=Decimal(str(bid + 0.0002)))


def test_symbol_readiness_skips_on_market_data_fetch_failure():
    adapter = _FakeAdapter(raise_on_fetch=True)
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=100.0, risk_percent_per_trade=0.25))
    assert result.verdict == readiness.SKIP_INSUFFICIENT_DATA


def test_symbol_readiness_skips_on_insufficient_atr_history():
    adapter = _FakeAdapter(candles=_flat_candles(5), quote=_quote())
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=100.0, risk_percent_per_trade=0.25))
    assert result.verdict == readiness.SKIP_INSUFFICIENT_DATA


def test_symbol_readiness_skips_when_risk_metadata_quorum_not_met(monkeypatch):
    from backend.brokers.mt5 import risk_calculator

    async def _fake_calc(**kwargs):
        return risk_calculator.CanonicalRiskResult(selected_loss_per_lot=None, selected_method=None, quorum_met=False, blocked=True, block_reason="INSUFFICIENT_TRUSTED_METHODS")

    monkeypatch.setattr(risk_calculator, "calculate_canonical_loss_per_lot", _fake_calc)
    adapter = _FakeAdapter(candles=_flat_candles(20, wick=0.0015), quote=_quote())
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=100.0, risk_percent_per_trade=0.25))
    assert result.verdict == readiness.SKIP_RISK_METADATA_UNTRUSTED


def test_symbol_readiness_skips_when_min_lot_exceeds_risk_budget(monkeypatch):
    from backend.brokers.mt5 import risk_calculator

    async def _fake_calc(**kwargs):
        # loss_per_lot deliberately huge -- even the smallest lot blows the $100 * 0.25% = $0.25 budget
        return risk_calculator.CanonicalRiskResult(selected_loss_per_lot=Decimal("500"), selected_method="contract_size", quorum_met=True, blocked=False)

    monkeypatch.setattr(risk_calculator, "calculate_canonical_loss_per_lot", _fake_calc)
    adapter = _FakeAdapter(candles=_flat_candles(20, wick=0.0015), quote=_quote())
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(volume_min=0.01), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=100.0, risk_percent_per_trade=0.25))
    assert result.verdict == readiness.SKIP_MIN_LOT_EXCEEDS_RISK
    assert result.loss_at_min_lot == pytest.approx(5.0)  # 500 * 0.01
    assert result.risk_budget == pytest.approx(0.25)


def test_symbol_readiness_ready_when_min_lot_fits_budget(monkeypatch):
    from backend.brokers.mt5 import risk_calculator

    async def _fake_calc(**kwargs):
        return risk_calculator.CanonicalRiskResult(selected_loss_per_lot=Decimal("10"), selected_method="contract_size", quorum_met=True, blocked=False)

    monkeypatch.setattr(risk_calculator, "calculate_canonical_loss_per_lot", _fake_calc)
    adapter = _FakeAdapter(candles=_flat_candles(20, wick=0.0015), quote=_quote())
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(volume_min=0.01), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=10000.0, risk_percent_per_trade=0.25))
    assert result.verdict == readiness.READY
    assert result.loss_at_min_lot == pytest.approx(0.1)  # 10 * 0.01
    assert result.risk_budget == pytest.approx(25.0)


def test_symbol_readiness_never_reports_a_volume_above_broker_minimum(monkeypatch):
    """There is no field anywhere in SymbolReadiness representing a suggested/adjusted volume --
    the only volume this module ever reasons about is the broker's own volume_min."""
    from backend.brokers.mt5 import risk_calculator

    async def _fake_calc(**kwargs):
        return risk_calculator.CanonicalRiskResult(selected_loss_per_lot=Decimal("10"), selected_method="contract_size", quorum_met=True, blocked=False)

    monkeypatch.setattr(risk_calculator, "calculate_canonical_loss_per_lot", _fake_calc)
    adapter = _FakeAdapter(candles=_flat_candles(20, wick=0.0015), quote=_quote())
    result = asyncio.run(readiness.symbol_readiness(instrument=_instrument(volume_min=0.01), adapter=adapter, mt5_client=None, account_currency="USD", hypothetical_balance=10000.0, risk_percent_per_trade=0.25))
    assert "suggested_volume" not in result.as_dict()
    assert result.as_dict()["volume_min"] == 0.01

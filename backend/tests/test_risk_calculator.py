from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.risk_calculator import (
    CRITICAL_MISMATCH,
    WARNING_MISMATCH,
    calculate_canonical_loss_per_lot,
    calculate_conservative_loss_per_lot_sync,
)


class _FakeNativeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, per_point: float):
        self.per_point = per_point

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
        return diff * self.per_point * volume


def _eurusd_clean() -> MT5Symbol:
    return MT5Symbol(
        symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _xauusd_broken() -> MT5Symbol:
    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, digits=2, point=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("0.1"), trade_tick_value_profit=Decimal("0.1"),
        trade_tick_value_loss=Decimal("0.1"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


# 1. order_calc_profit calculation available.
def test_order_calc_profit_estimate_available_when_client_provided():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    assert result.estimates["order_calc_profit"].available is True
    assert result.estimates["order_calc_profit"].loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))


# 2. order_calc_profit selected/considered.
def test_order_calc_profit_selected_when_it_is_the_most_conservative():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    assert result.selected_method == "order_calc_profit"
    assert result.selected_loss_per_lot == result.estimates["order_calc_profit"].loss_per_lot


# 3. contract method more conservative (than the deliberately understated tick_value method).
def test_contract_size_method_more_conservative_than_broken_tick_value():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken())
    assert result.estimates["contract_size"].loss_per_lot > result.estimates["tick_value"].loss_per_lot
    assert result.selected_method == "contract_size"


# 4. tick method more conservative (when contract_size is unavailable but tick data is intact).
def test_tick_value_method_used_when_contract_size_unavailable():
    symbol = _eurusd_clean().model_copy(update={"trade_contract_size": None})
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=symbol)
    assert result.estimates["contract_size"].available is False
    assert result.estimates["tick_value"].available is True
    assert result.selected_method == "tick_value"


# 5. largest conservative loss selected -- never the smallest.
def test_always_selects_largest_not_smallest_estimate():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    all_available = [est.loss_per_lot for est in result.estimates.values() if est.available and est.loss_per_lot]
    assert result.selected_loss_per_lot == max(all_available)
    assert result.selected_loss_per_lot != min(all_available)


# 6. 10x disagreement warning.
def test_ten_x_disagreement_produces_warning_code():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken())
    assert WARNING_MISMATCH in result.warning_codes
    assert result.max_disagreement_pct == pytest.approx(900.0, rel=0.01)  # contract(2359) vs tick(235.9)


# 7. critical disagreement blocks entry.
def test_critical_disagreement_sets_blocked_true():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), critical_pct=100.0)
    assert CRITICAL_MISMATCH in result.warning_codes
    assert result.blocked is True
    assert result.block_reason == CRITICAL_MISMATCH


# 8. XAUUSD regression -- the exact live-incident numbers, broker-native included.
def test_xauusd_regression_exact_incident_numbers():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("4279.93"), stop=Decimal("4256.34"), symbol_info=_xauusd_broken(), mt5_client=_FakeNativeMT5(100.0)))
    # Broker-confirmed ground truth for this exact trade: $2359/lot -> $235.90 at 0.10 lot.
    assert result.selected_loss_per_lot == pytest.approx(Decimal("2359.0"), rel=Decimal("0.001"))
    assert (result.selected_loss_per_lot * Decimal("0.10")) == pytest.approx(Decimal("235.90"), rel=Decimal("0.001"))
    assert result.blocked is True


# 9. normal EURUSD estimates agree within tolerance (no false positive mismatch on healthy data).
def test_eurusd_clean_metadata_estimates_agree_no_warning():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(100_000.0)))
    assert result.warning_codes == []
    assert result.blocked is False
    assert result.max_disagreement_pct == pytest.approx(0.0, abs=0.01)


# 10. SELL direction uses the correct broker-side sign (mirrors the BUY case).
def test_short_direction_produces_positive_loss_for_stop_above_entry():
    result = asyncio.run(calculate_canonical_loss_per_lot(direction="SHORT", entry=Decimal("1.10000"), stop=Decimal("1.10100"), symbol_info=_eurusd_clean(), mt5_client=_FakeNativeMT5(100_000.0)))
    assert result.selected_loss_per_lot > 0
    assert result.estimates["order_calc_profit"].loss_per_lot == pytest.approx(Decimal("100.0"), rel=Decimal("0.001"))

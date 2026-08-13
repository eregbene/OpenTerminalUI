from __future__ import annotations

from types import SimpleNamespace

from backend.brokers.mt5.trading_costs import (
    BROKER_REPORTED,
    CONFIG_FALLBACK,
    NONE_MODE,
    compute_trade_costs,
    resolve_commission,
)


def test_broker_reported_zero_commission_is_trusted(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", BROKER_REPORTED)

    commission, source = resolve_commission(broker_commission=0.0, volume=1.0)

    assert commission == 0.0
    assert source == BROKER_REPORTED


def test_broker_reported_missing_commission_uses_config_fallback(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", BROKER_REPORTED)
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")

    commission, source = resolve_commission(broker_commission=None, volume=0.70)

    assert commission == -4.90
    assert source == CONFIG_FALLBACK


def test_config_fallback_scales_linearly_for_spec_examples(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", CONFIG_FALLBACK)
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")

    assert resolve_commission(broker_commission=0.0, volume=1.00) == (-7.00, CONFIG_FALLBACK)
    assert resolve_commission(broker_commission=0.0, volume=0.70) == (-4.90, CONFIG_FALLBACK)
    assert resolve_commission(broker_commission=0.0, volume=0.10) == (-0.70, CONFIG_FALLBACK)
    assert resolve_commission(broker_commission=0.0, volume=0.06) == (-0.42, CONFIG_FALLBACK)


def test_none_mode_disables_commission(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", NONE_MODE)

    commission, source = resolve_commission(broker_commission=-12.0, volume=2.0)

    assert commission == 0.0
    assert source == NONE_MODE


def test_compute_trade_costs_keeps_gross_and_net_distinct_without_double_counting(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", BROKER_REPORTED)
    deals = [
        {"profit": 10.0, "commission": -0.70, "swap": -0.05, "fee": -0.10, "volume": 0.10},
        {"profit": -2.0, "commission": -0.70, "swap": 0.01, "fee": 0.0, "volume": 0.10},
    ]

    costs = compute_trade_costs(deals)

    assert costs.gross_pnl == 8.0
    assert costs.commission == -1.4
    assert costs.swap == -0.04
    assert costs.other_fees == -0.1
    assert costs.net_pnl == 6.46
    assert costs.total_trading_cost == 1.54
    assert costs.commission_source == BROKER_REPORTED


def test_partial_close_fallback_aggregates_each_deal_volume(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", CONFIG_FALLBACK)
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")
    deals = [
        SimpleNamespace(profit=12.0, commission=None, swap=0.0, fee=0.0, volume=0.10),
        SimpleNamespace(profit=24.0, commission=None, swap=0.0, fee=0.0, volume=0.70),
    ]

    costs = compute_trade_costs(deals)

    assert costs.gross_pnl == 36.0
    assert costs.commission == -5.60
    assert costs.net_pnl == 30.40
    assert costs.total_volume == 0.80
    assert costs.commission_per_lot_effective == -7.0
    assert costs.deal_count == 2


def test_mixed_commission_sources_are_reported(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", BROKER_REPORTED)
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")
    deals = [
        {"profit": 1.0, "commission": 0.0, "swap": 0.0, "fee": 0.0, "volume": 0.10},
        {"profit": 1.0, "commission": None, "swap": 0.0, "fee": 0.0, "volume": 0.10},
    ]

    costs = compute_trade_costs(deals)

    assert costs.commission == -0.70
    assert costs.net_pnl == 1.30
    assert costs.commission_source == "MIXED"

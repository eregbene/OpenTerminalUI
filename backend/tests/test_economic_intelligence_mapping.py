from __future__ import annotations

import pytest

from backend.economic_intelligence.event_mapping import higher_is_positive, is_central_bank_event, normalize_broker_symbol


def test_eurusd_maps_to_eur_and_usd():
    result = normalize_broker_symbol("EURUSD")
    assert result.base_currency == "EUR"
    assert result.quote_currency == "USD"
    assert set(result.currencies) == {"EUR", "USD"}


@pytest.mark.parametrize("symbol", ["EURUSD.a", "EURUSDm", "mEURUSD", "EURUSD-pro", "EURUSD#", "EURUSD_ecn"])
def test_broker_suffix_and_prefix_symbols_normalize_to_same_pair(symbol):
    result = normalize_broker_symbol(symbol)
    assert result.canonical_symbol == "EURUSD"
    assert result.base_currency == "EUR"
    assert result.quote_currency == "USD"


def test_xauusd_maps_correctly():
    result = normalize_broker_symbol("XAUUSD")
    assert result.base_currency == "XAU"
    assert result.quote_currency == "USD"


def test_is_central_bank_event_detects_fomc_and_speeches():
    assert is_central_bank_event("FOMC Press Conference")
    assert is_central_bank_event("ECB Interest Rate Decision")
    assert is_central_bank_event("Powell Speaks")
    assert not is_central_bank_event("CPI m/m")


def test_higher_is_positive_rules():
    assert higher_is_positive("nonfarm payrolls") is True
    assert higher_is_positive("unemployment rate") is False
    assert higher_is_positive("gdp q/q") is True
    assert higher_is_positive("fomc press conference speech") is None
    assert higher_is_positive("some totally unknown event") is None

"""2026-08-27 -- Gate C/D: converts an already-tier-capped risk_usd (the SAME dollar figure
MT5's calculate_risk_size decides, via BrokerOrderIntent) into a real cTrader lot size. The
critical invariant under test: cTrader's own minimum tradeable volume must never inflate risk
past what was already approved -- same rule the 2026-08-25/26 MT5 tier-cap fix enforces, and the
same rule CTraderAdapter._normalize_volume already enforces at the execution boundary. This is
the sizing-decision half of that guarantee, applied before an order is even built.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.brokers.ctrader.risk import loss_per_lot_usd, size_ctrader_position
from backend.brokers.models import BrokerSymbolSpec


def _eurusd_spec(*, volume_min="0.01", volume_step="0.01", volume_max="100") -> BrokerSymbolSpec:
    return BrokerSymbolSpec(
        instrument_id="FX:EURUSD", broker="ctrader", pip_position=4, pip_size=Decimal("0.0001"), digits=5,
        lot_size=Decimal("100000"), volume_min=Decimal(volume_min), volume_max=Decimal(volume_max), volume_step=Decimal(volume_step),
        contract_size=Decimal("100000"), margin_currency="USD",
    )


def test_loss_per_lot_matches_direct_quote_currency_math():
    # EURUSD, 20-pip stop, 100,000 units/lot, USD account -- straight multiply, no conversion.
    result = loss_per_lot_usd(symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0980"), spec=_eurusd_spec(), account_currency="USD")
    assert result == Decimal("200.00")  # 0.0020 * 100,000


def test_loss_per_lot_refuses_cross_currency_without_conversion_rate():
    """EURJPY's quote currency (JPY) != a USD account -- must refuse, never guess a cross rate."""
    spec = BrokerSymbolSpec(
        instrument_id="FX:EURJPY", broker="ctrader", pip_position=2, pip_size=Decimal("0.01"), digits=3,
        lot_size=Decimal("100000"), volume_min=Decimal("0.01"), volume_max=Decimal("100"), volume_step=Decimal("0.01"),
        contract_size=Decimal("100000"), margin_currency="JPY",
    )
    with pytest.raises(Exception, match="cross-currency conversion rate"):
        loss_per_lot_usd(symbol_name="FX:EURJPY", entry=Decimal("160.00"), stop=Decimal("159.80"), spec=spec, account_currency="USD")


def test_size_ctrader_position_never_inflates_past_approved_risk():
    """Tier C's tiny approved risk_usd, with a stop distance whose minimum-volume dollar risk
    would exceed it -- must REJECT, never round up to volume_min."""
    decision = size_ctrader_position(
        symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0000"),  # huge 1000-pip stop
        risk_usd=Decimal("5.00"), spec=_eurusd_spec(), account_currency="USD",
    )
    assert decision.status == "REJECTED"
    assert any("VOLUME_BELOW_MINIMUM_RISK_TOO_HIGH" in r for r in decision.reasons)
    assert decision.volume == Decimal("0")


def test_size_ctrader_position_floors_to_step_never_rounds_up():
    decision = size_ctrader_position(
        symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0980"),
        risk_usd=Decimal("219.99"), spec=_eurusd_spec(), account_currency="USD",  # 219.99/200 = 1.09995 lots -> floors to 1.09
    )
    assert decision.status == "APPROVED"
    assert decision.volume == Decimal("1.09")
    assert decision.projected_loss_usd <= Decimal("219.99")  # never exceeds the approved risk


def test_size_ctrader_position_respects_tier_cap_style_scaling():
    """Mirrors the exact scenario the real MT5 tier-cap verification used: same entry/stop
    geometry, three different risk_usd values (as Tier A/B/C would produce) -> volume should
    scale down proportionally, never converge back toward Tier A's size."""
    common = dict(symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0980"), spec=_eurusd_spec(), account_currency="USD")
    tier_a = size_ctrader_position(risk_usd=Decimal("24.00"), **common)
    tier_b = size_ctrader_position(risk_usd=Decimal("12.00"), **common)
    tier_c = size_ctrader_position(risk_usd=Decimal("6.00"), **common)
    assert tier_a.status == tier_b.status == tier_c.status == "APPROVED"
    assert tier_a.volume > tier_b.volume > tier_c.volume
    assert tier_b.volume == pytest.approx(tier_a.volume / 2, rel=0.05)
    assert tier_c.volume == pytest.approx(tier_a.volume / 4, rel=0.05)


def test_size_ctrader_position_never_exceeds_volume_max():
    decision = size_ctrader_position(
        symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0999"),  # 1-pip stop -> huge lot count
        risk_usd=Decimal("100000"), spec=_eurusd_spec(volume_max="50"), account_currency="USD",
    )
    assert decision.status == "APPROVED"
    assert decision.volume == Decimal("50")


def test_size_ctrader_position_rejects_invalid_risk_usd():
    decision = size_ctrader_position(
        symbol_name="FX:EURUSD", entry=Decimal("1.1000"), stop=Decimal("1.0980"),
        risk_usd=Decimal("0"), spec=_eurusd_spec(), account_currency="USD",
    )
    assert decision.status == "REJECTED"
    assert decision.reasons == ["INVALID_RISK_USD"]

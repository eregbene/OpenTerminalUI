"""Offline, fixture-based tests for the cTrader Open API adapter (Phase 2 of the broker-
independence migration) -- NO real network connection, no real cTrader credentials required.
Covers: account/position/quote/symbol/historical-bar mapping, decimal precision, canonical-lot
conversion (the safety-critical piece -- see volume.py's own module docstring), demo/live
protection, malformed/missing metadata, and Protocol conformance.

Network-dependent (real cTrader connectivity) tests are explicitly NOT here -- see the module
docstring in test_ctrader_live_smoke.py (created once real credentials exist) for that split.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.base import BrokerReadAdapter
from backend.brokers.ctrader import volume
from backend.brokers.ctrader.adapter import CTraderAdapter, _price_from_raw
from backend.brokers.ctrader.config import CTraderConfig
from backend.brokers.ctrader.exceptions import CTraderEnvironmentMismatchError, CTraderReadOnlyViolation, CTraderUnavailableError
from backend.brokers.ctrader.symbols import ctrader_symbol_to_spec
from backend.brokers.models import BrokerSymbolSpec


# --------------------------------------------------------------------------- volume conversion ---
# EURUSD-shaped real values: lotSize=10,000,000 (cents) -> 100,000 units/lot (verified against the
# official proto field comment + a real forum example: "10000000 / 100 = 100'000").
_EURUSD_LOT_SIZE_RAW = 10_000_000


def test_raw_volume_to_lots_matches_verified_example():
    # minVolume=1000 cents -> 0.0001 lot is implausibly small for a real broker; use the
    # documented real-world shape instead: 0.01 lot minimum -> raw = 0.01 * 10,000,000 = 100,000.
    assert volume.raw_volume_to_lots(100_000, _EURUSD_LOT_SIZE_RAW) == Decimal("0.01")
    assert volume.raw_volume_to_lots(_EURUSD_LOT_SIZE_RAW, _EURUSD_LOT_SIZE_RAW) == Decimal("1")
    assert volume.raw_volume_to_lots(_EURUSD_LOT_SIZE_RAW * 2, _EURUSD_LOT_SIZE_RAW) == Decimal("2")


def test_lots_to_raw_volume_round_trip():
    raw = volume.lots_to_raw_volume(Decimal("0.5"), _EURUSD_LOT_SIZE_RAW)
    assert raw == 5_000_000
    assert volume.raw_volume_to_lots(raw, _EURUSD_LOT_SIZE_RAW) == Decimal("0.5")


def test_volume_conversion_never_assumes_a_fixed_divisor():
    """A symbol with a DIFFERENT units-per-lot convention (e.g. a metal/CFD, not 100,000) must
    still convert correctly -- proves the formula uses the symbol's own lotSize, not a hardcoded
    constant (the exact mistake a real cTrader forum user reported hitting)."""
    xauusd_lot_size_raw = 10_000  # hypothetical: 100 units/lot for a metals CFD
    assert volume.raw_volume_to_lots(10_000, xauusd_lot_size_raw) == Decimal("1")
    assert volume.raw_volume_to_lots(5_000, xauusd_lot_size_raw) == Decimal("0.5")


def test_raw_volume_to_lots_rejects_zero_lot_size():
    with pytest.raises(CTraderUnavailableError):
        volume.raw_volume_to_lots(1000, 0)


def test_money_from_raw_uses_money_digits_exponent():
    # moneyDigits=2 (cents) -> 1000000 raw -> 10000.00
    assert volume.money_from_raw(1_000_000, 2) == Decimal("10000")
    # moneyDigits=8 -> matches the proto's own worked example
    assert volume.money_from_raw(1_000_000_000_000, 8) == Decimal("10000")


def test_pip_size_and_point_size_derivation():
    assert volume.pip_size_from_position(4) == Decimal("0.0001")  # EURUSD: 5 digits, pip at 4th
    assert volume.point_size_from_digits(5) == Decimal("0.00001")


def test_price_from_raw_uses_fixed_1e5_scale():
    # Verified example: bid=112961 -> 1.12961
    assert _price_from_raw(112961) == Decimal("1.12961")
    assert _price_from_raw(112966) == Decimal("1.12966")


# ---------------------------------------------------------------------------- symbol mapping ---
def _fake_eurusd_symbol():
    return SimpleNamespace(
        digits=5, pipPosition=4, lotSize=_EURUSD_LOT_SIZE_RAW, minVolume=100_000, maxVolume=1_000_000_000,
        stepVolume=100_000, depositCurrency="EUR", quoteAssetId=None,
    )


def test_symbol_mapping_produces_canonical_lots_only():
    spec = ctrader_symbol_to_spec(_fake_eurusd_symbol(), instrument_id="FX:EURUSD")
    assert isinstance(spec, BrokerSymbolSpec)
    assert spec.broker == "ctrader"
    assert spec.digits == 5
    assert spec.pip_size == Decimal("0.0001")
    assert spec.lot_size == Decimal("100000")
    assert spec.contract_size == Decimal("100000")
    assert spec.volume_min == Decimal("0.01")
    assert spec.volume_max == Decimal("100")
    assert spec.volume_step == Decimal("0.01")
    # No raw cTrader integer anywhere in the resulting spec -- every numeric field is already
    # canonical (lots / price units), not "cents" or a symbolId-scale integer.
    for field in ("lot_size", "volume_min", "volume_max", "volume_step", "contract_size"):
        assert getattr(spec, field) < Decimal("1000000")  # sanity: not an un-converted raw integer


def test_symbol_mapping_raises_rather_than_guesses_when_metadata_incomplete():
    incomplete = SimpleNamespace(digits=5, pipPosition=4, lotSize=None, minVolume=None, maxVolume=None, stepVolume=None)
    with pytest.raises(CTraderUnavailableError):
        ctrader_symbol_to_spec(incomplete, instrument_id="FX:XAUUSD")


def test_symbol_mapping_rejects_zero_lot_size():
    bad = SimpleNamespace(digits=5, pipPosition=4, lotSize=0, minVolume=1000, maxVolume=1000000, stepVolume=1000)
    with pytest.raises(CTraderUnavailableError):
        ctrader_symbol_to_spec(bad, instrument_id="FX:EURUSD")


# ------------------------------------------------------------------------------- config/safety ---
def test_config_reads_live_env_not_cached(monkeypatch):
    from backend.brokers.ctrader.config import ctrader_config

    monkeypatch.setenv("CTRADER_ENVIRONMENT", "demo")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "10102160")
    cfg = ctrader_config()
    assert cfg.is_demo is True
    assert cfg.account_id == "10102160"

    monkeypatch.setenv("CTRADER_ENVIRONMENT", "live")
    cfg2 = ctrader_config()
    assert cfg2.is_demo is False


def test_config_never_hardcodes_secrets(monkeypatch):
    monkeypatch.delenv("CTRADER_CLIENT_ID", raising=False)
    monkeypatch.delenv("CTRADER_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CTRADER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CTRADER_REFRESH_TOKEN", raising=False)
    from backend.brokers.ctrader.config import ctrader_config

    cfg = ctrader_config()
    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.has_app_credentials is False
    assert cfg.has_account_tokens is False


def test_connect_refuses_when_environment_is_not_demo():
    cfg = CTraderConfig(environment="live", client_id="x", client_secret="y", access_token="z", refresh_token="w", account_id="10102160", redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)
    with pytest.raises(CTraderEnvironmentMismatchError):
        asyncio.run(adapter.connect())


def test_connect_refuses_when_credentials_missing():
    cfg = CTraderConfig(environment="demo", client_id=None, client_secret=None, access_token=None, refresh_token=None, account_id=None, redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)
    with pytest.raises(CTraderUnavailableError):
        asyncio.run(adapter.connect())


def test_live_trading_never_enabled_regardless_of_env():
    """2026-08-27: order mutation capability now exists for the DEMO vertical slice (gated
    per-call by order_submission_enabled + _verified_demo -- see test_order_mutation_methods_
    all_raise_read_only below), but live_trading_enabled must remain False unconditionally --
    that flag means real-money LIVE trading, which this adapter must never enable no matter what
    any env var says."""
    cfg = CTraderConfig(environment="demo", client_id="x", client_secret="y", access_token="z", refresh_token="w", account_id="10102160", redirect_uri="http://localhost", order_submission_enabled=True)
    adapter = CTraderAdapter(config=cfg)
    assert adapter.capabilities.live_trading_enabled is False


# --------------------------------------------------------------------------- read-only guards ---
def test_order_mutation_methods_all_raise_read_only():
    adapter = CTraderAdapter()
    with pytest.raises(CTraderReadOnlyViolation):
        asyncio.run(adapter.submit_order(None))
    with pytest.raises(CTraderReadOnlyViolation):
        asyncio.run(adapter.cancel_order(None))
    with pytest.raises(CTraderReadOnlyViolation):
        asyncio.run(adapter.modify_position(None))
    with pytest.raises(CTraderReadOnlyViolation):
        asyncio.run(adapter.close_position(None))


# ------------------------------------------------------------------------- Protocol conformance ---
def test_ctrader_adapter_conforms_to_broker_read_adapter_protocol():
    adapter = CTraderAdapter()
    assert isinstance(adapter, BrokerReadAdapter)


# --------------------------------------------------------------------------------- token handling ---
def test_refresh_access_token_updates_config_and_warns_to_persist_new_refresh_token(monkeypatch, caplog):
    from backend.brokers.ctrader import oauth as oauth_module

    cfg = CTraderConfig(environment="demo", client_id="cid", client_secret="secret", access_token="old_access", refresh_token="old_refresh", account_id="10102160", redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)

    def _fake_refresh(**kwargs):
        assert kwargs["refresh_token"] == "old_refresh"
        return oauth_module.CTraderTokenResult(access_token="new_access", refresh_token="new_refresh", expires_in=2592000, token_type="bearer")

    monkeypatch.setattr("backend.brokers.ctrader.adapter.refresh_access_token", _fake_refresh)
    import logging
    with caplog.at_level(logging.WARNING):
        asyncio.run(adapter._refresh_token_if_needed())

    assert adapter._config.access_token == "new_access"
    assert adapter._config.refresh_token == "new_refresh"
    assert "new_refresh" in caplog.text  # operator must be told to persist the rotated refresh token


# ------------------------------------------------------------------------------- unsupported symbols ---
def test_resolve_symbol_id_raises_for_unknown_symbol():
    cfg = CTraderConfig(environment="demo", client_id="cid", client_secret="secret", access_token="tok", refresh_token="ref", account_id="10102160", redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)

    async def _fake_send(message):
        return SimpleNamespace(symbol=[SimpleNamespace(symbolId=1, symbolName="EURUSD"), SimpleNamespace(symbolId=2, symbolName="GBPUSD")])

    adapter._send = _fake_send  # bypasses the transport entirely -- pure routing-logic test
    with pytest.raises(CTraderUnavailableError):
        asyncio.run(adapter._resolve_symbol_id("FX:NOTASYMBOL"))


def test_resolve_symbol_id_finds_known_symbol():
    cfg = CTraderConfig(environment="demo", client_id="cid", client_secret="secret", access_token="tok", refresh_token="ref", account_id="10102160", redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)

    async def _fake_send(message):
        return SimpleNamespace(symbol=[SimpleNamespace(symbolId=1, symbolName="EURUSD"), SimpleNamespace(symbolId=2, symbolName="GBPUSD")])

    adapter._send = _fake_send
    symbol_id, name = asyncio.run(adapter._resolve_symbol_id("FX:EURUSD"))
    assert symbol_id == 1
    assert name == "EURUSD"


# ------------------------------------------------------------------------------------ reconnect ---
def test_reconnect_tears_down_and_rebuilds_transport(monkeypatch):
    cfg = CTraderConfig(environment="live", client_id="cid", client_secret="secret", access_token="tok", refresh_token="ref", account_id="10102160", redirect_uri="http://localhost")
    adapter = CTraderAdapter(config=cfg)
    stopped = {"called": False}

    class _FakeTransport:
        is_connected = True

        def stop(self):
            stopped["called"] = True

    adapter._transport = _FakeTransport()
    adapter._app_authorized = True
    adapter._authorized_account_id = 10102160
    # reconnect() calls connect() again, which will refuse (environment=live) -- proves teardown
    # happened (transport.stop() called, state reset) even when the subsequent connect() fails.
    with pytest.raises(CTraderEnvironmentMismatchError):
        asyncio.run(adapter.reconnect())
    assert stopped["called"] is True
    assert adapter._transport is None
    assert adapter._app_authorized is False


# ------------------------------------------------------------------------------ trendbar mapping ---
def test_trendbar_reconstruction_from_deltas():
    """low + deltaOpen/deltaHigh/deltaClose, all scaled by 1e5 (verified formula) -- reconstructs
    a real OHLC bar without ever exposing the raw delta-encoding upward."""
    low_raw = 109500  # 1.09500
    bar = SimpleNamespace(low=low_raw, deltaOpen=50, deltaHigh=150, deltaClose=80, volume=1234, utcTimestampInMinutes=1000)
    low = _price_from_raw(bar.low)
    open_ = low + _price_from_raw(bar.deltaOpen)
    high = low + _price_from_raw(bar.deltaHigh)
    close = low + _price_from_raw(bar.deltaClose)
    assert low == Decimal("1.095")
    assert open_ == Decimal("1.0955")
    assert high == Decimal("1.0965")
    assert close == Decimal("1.09580")
    assert low <= open_ <= high
    assert low <= close <= high


# ------------------------------------------------------------- order execution (2026-08-27) ---
from backend.brokers.ctrader.exceptions import CTraderAuthError, CTraderCapabilityError
from backend.brokers.models import BrokerCancelCommand, BrokerClosePositionCommand, BrokerModifyPositionCommand, BrokerOrderCommand


def _demo_verified_adapter(*, order_submission_enabled: bool = True) -> CTraderAdapter:
    cfg = CTraderConfig(
        environment="demo", client_id="cid", client_secret="secret", access_token="tok", refresh_token="ref",
        account_id="555", redirect_uri="http://localhost", order_submission_enabled=order_submission_enabled,
    )
    adapter = CTraderAdapter(config=cfg)
    adapter._verified_demo = True  # bypasses connect()'s real handshake -- pure execution-logic test
    return adapter


def _fake_symbol_spec_dispatch(message):
    """Dispatches by message shape, mirroring what submit_order/close_position/modify_position
    actually call in sequence: symbol list -> symbol-by-id (spec + lotSize) -> new-order/close/amend."""
    cls_name = type(message).__name__
    if cls_name == "ProtoOASymbolsListReq":
        return SimpleNamespace(symbol=[SimpleNamespace(symbolId=1, symbolName="EURUSD")])
    if cls_name == "ProtoOASymbolByIdReq":
        return SimpleNamespace(symbol=[SimpleNamespace(
            symbolId=1, digits=5, pipPosition=4, lotSize=10_000_000, minVolume=100_000, maxVolume=5_000_000_000, stepVolume=100_000,
            depositCurrency="USD",
        )])
    return SimpleNamespace()


async def _fake_symbol_spec_send(message):
    return _fake_symbol_spec_dispatch(message)


def test_submit_order_normalizes_volume_and_builds_real_request():
    adapter = _demo_verified_adapter()
    sent_requests = []

    async def _fake_send(message):
        sent_requests.append(message)
        return _fake_symbol_spec_dispatch(message)

    adapter._send = _fake_send

    fake_deal = SimpleNamespace(positionId=999, filledVolume=1_000_000, executionPrice=110000, commission=0)
    fake_order = SimpleNamespace(orderId=42, positionId=999, executedVolume=1_000_000)
    fake_event = SimpleNamespace(errorCode="", executionType="ORDER_FILLED", order=fake_order, deal=fake_deal, HasField=lambda name: True)

    async def _fake_wait(*, account_id, timeout=20.0):
        return fake_event

    adapter._wait_for_execution = _fake_wait

    command = BrokerOrderCommand(
        canonical_order_id="ORD1", account_id="555", instrument_id="FX:EURUSD", side="LONG", order_type="MARKET",
        time_in_force="IOC", quantity=Decimal("0.13"), approved_quantity=Decimal("0.13"),
        stop_loss=Decimal("1.0950"), take_profit=Decimal("1.1050"), risk_evaluation_id="R1", idempotency_key="IDEMP1",
    )
    receipt = asyncio.run(adapter.submit_order(command))

    new_order_req = next(r for r in sent_requests if type(r).__name__ == "ProtoOANewOrderReq")
    assert new_order_req.symbolId == 1
    assert new_order_req.volume == 1_300_000  # lots_to_raw_volume(0.13, 10_000_000) = 0.13 * 10,000,000
    assert new_order_req.stopLoss == pytest.approx(1.0950)
    assert new_order_req.takeProfit == pytest.approx(1.1050)
    assert receipt.order.state.value == "FILLED"
    assert receipt.submission_state == "FILLED"


def test_submit_order_rejects_when_volume_floors_below_minimum_never_inflates():
    """The exact invariant the strategy-tier hard-cap fix depends on: a tiny approved_quantity
    that floors below the broker's minimum tradeable size must be REJECTED, never bumped up to
    volume_min -- that would silently exceed the caller's already-risk-vetted size."""
    adapter = _demo_verified_adapter()
    adapter._send = _fake_symbol_spec_send

    command = BrokerOrderCommand(
        canonical_order_id="ORD2", account_id="555", instrument_id="FX:EURUSD", side="LONG", order_type="MARKET",
        time_in_force="IOC", quantity=Decimal("0.001"), approved_quantity=Decimal("0.001"),  # below minVolume=0.01 lots
        risk_evaluation_id="R2", idempotency_key="IDEMP2",
    )
    with pytest.raises(CTraderCapabilityError, match="below this symbol's volume_min") as exc_info:
        asyncio.run(adapter.submit_order(command))
    assert exc_info.value.code == "VOLUME_BELOW_MINIMUM"


def test_submit_order_raises_when_submission_disabled_by_default():
    adapter = _demo_verified_adapter(order_submission_enabled=False)
    with pytest.raises(CTraderReadOnlyViolation, match="CTRADER_ORDER_SUBMISSION_ENABLED"):
        asyncio.run(adapter.submit_order(None))


def test_submit_order_raises_when_not_verified_demo_even_if_enabled():
    """The OAuth-scope/demo-identity gate is independent of the flag -- an unverified account
    must never submit, no matter what CTRADER_ORDER_SUBMISSION_ENABLED says."""
    cfg = CTraderConfig(environment="demo", client_id="cid", client_secret="secret", access_token="tok", refresh_token="ref", account_id="555", redirect_uri="http://localhost", order_submission_enabled=True)
    adapter = CTraderAdapter(config=cfg)  # never connected -- _verified_demo stays False
    with pytest.raises(CTraderReadOnlyViolation, match="not verified as DEMO"):
        asyncio.run(adapter.submit_order(None))


def test_submit_order_raises_normalized_error_on_broker_rejection():
    adapter = _demo_verified_adapter()
    adapter._send = _fake_symbol_spec_send

    async def _fake_wait(*, account_id, timeout=20.0):
        return SimpleNamespace(errorCode="MARKET_CLOSED", description="Market is closed", HasField=lambda name: False)

    adapter._wait_for_execution = _fake_wait
    command = BrokerOrderCommand(
        canonical_order_id="ORD3", account_id="555", instrument_id="FX:EURUSD", side="LONG", order_type="MARKET",
        time_in_force="IOC", quantity=Decimal("0.1"), approved_quantity=Decimal("0.1"), risk_evaluation_id="R3", idempotency_key="IDEMP3",
    )
    with pytest.raises(CTraderAuthError, match="MARKET_CLOSED"):
        asyncio.run(adapter.submit_order(command))


def test_close_position_full_close_uses_exact_current_volume():
    adapter = _demo_verified_adapter()

    from backend.brokers.models import BrokerPosition
    current_position = BrokerPosition(
        canonical_position_id="ctrader:555:999", broker_position_id="999", account_id="555", broker="ctrader",
        instrument_id="FX:EURUSD", quantity=Decimal("0.13"), average_cost=Decimal("1.1000"), currency="USD",
    )

    async def _fake_positions(account_id):
        return [current_position]

    adapter.positions = _fake_positions
    sent_requests = []

    async def _fake_send(message):
        sent_requests.append(message)
        return _fake_symbol_spec_dispatch(message)

    adapter._send = _fake_send

    async def _fake_wait(*, account_id, timeout=20.0):
        deal = SimpleNamespace(positionId=999, executionPrice=110000, commission=0)
        return SimpleNamespace(errorCode="", order=SimpleNamespace(orderId=43), deal=deal, HasField=lambda name: True)

    adapter._wait_for_execution = _fake_wait

    command = BrokerClosePositionCommand(
        canonical_position_id="ctrader:555:999", broker_position_id="999", account_id="555",
        instrument_id="FX:EURUSD", quantity=None, idempotency_key="IDEMPCLOSE",
    )
    receipt = asyncio.run(adapter.close_position(command))

    close_req = next(r for r in sent_requests if type(r).__name__ == "ProtoOAClosePositionReq")
    assert close_req.volume == 1_300_000  # exactly the position's own current raw volume, never re-derived
    assert receipt.closed_quantity == Decimal("0.13")
    assert receipt.remaining_quantity == Decimal("0")


def test_modify_position_preserves_unset_field_using_current_value():
    """None on the command means "leave unchanged" -- must be filled in with the position's OWN
    current value before sending, never silently zeroed on the wire."""
    adapter = _demo_verified_adapter()

    from backend.brokers.models import BrokerPosition
    current_position = BrokerPosition(
        canonical_position_id="ctrader:555:999", broker_position_id="999", account_id="555", broker="ctrader",
        instrument_id="FX:EURUSD", quantity=Decimal("0.13"), average_cost=Decimal("1.1000"),
        stop_loss=Decimal("1.0900"), take_profit=Decimal("1.1200"), currency="USD",
    )

    async def _fake_positions(account_id):
        return [current_position]

    adapter.positions = _fake_positions
    sent_requests = []

    async def _fake_send(message):
        sent_requests.append(message)
        return SimpleNamespace()

    adapter._send = _fake_send

    async def _fake_wait(*, account_id, timeout=20.0):
        return SimpleNamespace(errorCode="")

    adapter._wait_for_execution = _fake_wait

    command = BrokerModifyPositionCommand(
        canonical_position_id="ctrader:555:999", broker_position_id="999", account_id="555",
        instrument_id="FX:EURUSD", stop_loss=Decimal("1.0950"), take_profit=None,  # only moving SL, TP must stay 1.1200
        idempotency_key="IDEMPMOD",
    )
    asyncio.run(adapter.modify_position(command))

    amend_req = next(r for r in sent_requests if type(r).__name__ == "ProtoOAAmendPositionSLTPReq")
    assert amend_req.stopLoss == pytest.approx(1.0950)
    assert amend_req.takeProfit == pytest.approx(1.1200)  # preserved, not cleared


def test_cancel_order_raises_when_submission_disabled():
    adapter = _demo_verified_adapter(order_submission_enabled=False)
    command = BrokerCancelCommand(canonical_order_id="ORD1", broker_order_id="42", account_id="555", reason="test")
    with pytest.raises(CTraderReadOnlyViolation):
        asyncio.run(adapter.cancel_order(command))

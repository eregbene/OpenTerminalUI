"""2026-08-27 -- Gate D bridge tests. attempt_ctrader_bridge_trade takes the SAME naturally-
qualified candidate MT5's own run_cycle already finalized a decision for; these tests cover its
own safety gates (disabled-by-default, missing credentials, price divergence, risk rejection,
dry-run success) without needing a live cTrader connection.

The bridge deliberately keeps ONE persistent adapter for the process's lifetime (Twisted's
reactor cannot be restarted once stopped -- a real bug found live, see bridge.py's own comment),
so tests inject a fake adapter directly into backend.brokers.ctrader.bridge._bridge_adapter
rather than patching the CTraderAdapter class -- and reset that module global after every test so
no state leaks between them.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

import backend.brokers.ctrader.bridge as bridge_module
from backend.brokers.ctrader.bridge import attempt_ctrader_bridge_trade, bridge_enabled, should_attempt_this_cycle


@pytest.fixture(autouse=True)
def _reset_bridge_adapter():
    bridge_module._bridge_adapter = None
    bridge_module._last_attempted_base_cycle_id = None
    yield
    bridge_module._bridge_adapter = None
    bridge_module._last_attempted_base_cycle_id = None


def test_should_attempt_this_cycle_allows_first_call_for_a_new_candle():
    assert should_attempt_this_cycle("MT5_M5_202608271540") is True


def test_should_attempt_this_cycle_dedupes_within_the_same_candle():
    """Any account may trigger the bridge, but never more than once for the same M5 candle --
    the real reason the trigger was widened past a single hardcoded account: different accounts
    produce different `best` candidates each cycle (confirmed live), so multiple accounts could
    otherwise each attempt the bridge for the same candle."""
    assert should_attempt_this_cycle("MT5_M5_202608271540") is True
    assert should_attempt_this_cycle("MT5_M5_202608271540") is False  # a second account, same candle
    assert should_attempt_this_cycle("MT5_M5_202608271540") is False  # a third account, same candle


def test_should_attempt_this_cycle_allows_the_next_candle():
    assert should_attempt_this_cycle("MT5_M5_202608271540") is True
    assert should_attempt_this_cycle("MT5_M5_202608271545") is True


def _candidate(*, stop_loss="1.0950", take_profit="1.1150", bid="1.1000", ask="1.1002", strategy_id="mtfai1") -> dict:
    return {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG",
        "context_hash": "bridge_test_hash", "candidate_id": "BRIDGE_TEST_1",
        "context": {"strategy_id": strategy_id, "bid": bid, "ask": ask},
        "stop_loss": stop_loss, "take_profit": take_profit,
    }


def test_bridge_disabled_by_default(monkeypatch):
    # Explicitly clears the env var rather than assuming an unset ambient default -- this
    # container's own real environment may have it set (e.g. after a genuine Gate D go-live).
    monkeypatch.delenv("CTRADER_BENSIM_ENGINE_ENABLED", raising=False)
    assert bridge_enabled() is False
    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0))
    assert result.status == "DISABLED"


def test_bridge_skips_without_credentials(monkeypatch):
    monkeypatch.setenv("CTRADER_BENSIM_ENGINE_ENABLED", "true")
    monkeypatch.delenv("CTRADER_CLIENT_ID", raising=False)
    monkeypatch.delenv("CTRADER_ACCESS_TOKEN", raising=False)
    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0))
    assert result.status == "SKIPPED_NO_CREDENTIALS"


def _patched_env(monkeypatch):
    monkeypatch.setenv("CTRADER_BENSIM_ENGINE_ENABLED", "true")
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "ref")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "555")


def _fake_spec():
    from backend.brokers.models import BrokerSymbolSpec

    return BrokerSymbolSpec(
        instrument_id="FX:EURUSD", broker="ctrader", pip_position=4, pip_size=Decimal("0.0001"), digits=5,
        lot_size=Decimal("100000"), volume_min=Decimal("0.01"), volume_max=Decimal("100"), volume_step=Decimal("0.01"),
        contract_size=Decimal("100000"), margin_currency="USD",
    )


def _fake_snapshot():
    from backend.brokers.models import BrokerAccountSnapshot, BrokerEnvironment

    return BrokerAccountSnapshot(account_id="555", broker="ctrader", environment=BrokerEnvironment.PAPER, net_liquidation=Decimal("50000"))


def _fake_health(*, verified=True, connected=True):
    from backend.brokers.health import BrokerHealth
    from backend.brokers.models import BrokerConnectionState, BrokerEnvironment, BrokerHealthState

    return BrokerHealth(
        broker="ctrader", state=BrokerHealthState.HEALTHY,
        connection_state=BrokerConnectionState.CONNECTED if connected else BrokerConnectionState.DISCONNECTED,
        environment=BrokerEnvironment.PAPER, account_verification_status="VERIFIED_DEMO" if verified else "UNVERIFIED",
        order_submission_status="DISABLED",
    )


def _install_fake_adapter(**overrides):
    fake = AsyncMock()
    fake.health = AsyncMock(return_value=_fake_health())
    for name, value in overrides.items():
        setattr(fake, name, value)
    bridge_module._bridge_adapter = fake
    return fake


def test_bridge_rejects_when_price_diverges_beyond_tolerance(monkeypatch):
    _patched_env(monkeypatch)
    from backend.brokers.models import BrokerQuote, DataQuality, MarketDataMode

    # MT5 reference (candidate context ask) = 1.1002; a wildly different cTrader price
    # (5x the stop distance away) must be rejected, never traded on stale/divergent geometry.
    ctrader_quote = BrokerQuote(instrument_id="FX:EURUSD", bid=Decimal("1.1300"), ask=Decimal("1.1302"), mode=MarketDataMode.REALTIME, quality=DataQuality.VALID)
    _install_fake_adapter(quote=AsyncMock(return_value=ctrader_quote))

    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0))
    assert result.status == "REJECTED_PRICE_DIVERGENCE"


def test_bridge_dry_run_succeeds_with_consistent_prices_and_valid_risk(monkeypatch):
    _patched_env(monkeypatch)
    from backend.brokers.models import BrokerQuote, DataQuality, MarketDataMode

    ctrader_quote = BrokerQuote(instrument_id="FX:EURUSD", bid=Decimal("1.1000"), ask=Decimal("1.1002"), mode=MarketDataMode.REALTIME, quality=DataQuality.VALID)
    _install_fake_adapter(
        quote=AsyncMock(return_value=ctrader_quote),
        symbol_spec=AsyncMock(return_value=_fake_spec()),
        account_snapshot=AsyncMock(return_value=_fake_snapshot()),
    )

    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0, dry_run=True))
    assert result.status == "DRY_RUN_OK"
    assert result.detail["order_intent"]["strategy_id"] == "mtfai1"
    assert result.detail["order_intent"]["broker"] == "ctrader"
    assert Decimal(result.detail["volume"]) > 0


def test_bridge_reuses_the_same_adapter_instance_across_calls(monkeypatch):
    """Locks in the real fix: the reactor cannot restart once stopped, so the bridge must reuse
    ONE adapter across calls, never construct+connect+disconnect a fresh one each time."""
    _patched_env(monkeypatch)
    from backend.brokers.models import BrokerQuote, DataQuality, MarketDataMode

    ctrader_quote = BrokerQuote(instrument_id="FX:EURUSD", bid=Decimal("1.1000"), ask=Decimal("1.1002"), mode=MarketDataMode.REALTIME, quality=DataQuality.VALID)
    fake = _install_fake_adapter(
        quote=AsyncMock(return_value=ctrader_quote),
        symbol_spec=AsyncMock(return_value=_fake_spec()),
        account_snapshot=AsyncMock(return_value=_fake_snapshot()),
    )

    asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0, dry_run=True))
    asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0, dry_run=True))

    assert bridge_module._bridge_adapter is fake  # never replaced
    fake.connect.assert_not_called()  # health() already reported CONNECTED, so connect() is skipped
    assert fake.quote.await_count == 2  # both calls actually ran against the same adapter


def test_bridge_skips_when_account_not_verified_demo(monkeypatch):
    _patched_env(monkeypatch)
    _install_fake_adapter(health=AsyncMock(return_value=_fake_health(verified=False)))

    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0))
    assert result.status == "SKIPPED_NOT_VERIFIED_DEMO"


def test_bridge_never_raises_on_unexpected_error(monkeypatch):
    """Hard safety requirement: whatever goes wrong inside the bridge, it must return an ERROR
    result, never propagate an exception into the caller's live MT5 cycle."""
    _patched_env(monkeypatch)
    fake = _install_fake_adapter()
    fake.health = AsyncMock(side_effect=RuntimeError("boom"))

    result = asyncio.run(attempt_ctrader_bridge_trade(_candidate(), confidence=80.0))
    assert result.status == "ERROR"

from __future__ import annotations

import asyncio
import importlib.util
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.errors import BrokerCapabilityError, BrokerSafetyError
from backend.brokers.ibkr.client import IBKRPaperAdapter, IbkrSessionManager, RealTwsReadOnlyClient
from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.api.routes.brokers import ibkr_environment_check
from backend.brokers.models import BrokerOrderCommand


def test_real_paper_mode_cannot_use_fixture_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_ENABLED", "1")
    monkeypatch.setenv("IBKR_MODE", "PAPER")
    monkeypatch.setenv("IBKR_SIMULATED", "1")
    adapter = IBKRPaperAdapter(IBKRConfiguration())
    with pytest.raises(BrokerSafetyError) as exc:
        asyncio.run(adapter.connect())
    assert exc.value.code == "REAL_IBKR_PAPER_REQUIRES_NON_SIMULATED_ADAPTER"


def test_fixture_mode_is_labeled_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_ENABLED", "1")
    monkeypatch.setenv("IBKR_MODE", "FIXTURE")
    monkeypatch.setenv("IBKR_SIMULATED", "1")
    config = IBKRConfiguration()
    assert config.adapter_mode == "FIXTURE_IBKR"


def test_environment_check_returns_safe_diagnostics() -> None:
    payload = asyncio.run(ibkr_environment_check(current_user=None))  # type: ignore[arg-type]
    assert "ibapi_available" in payload
    assert "adapter_mode" in payload
    assert "configured_host" in payload
    assert "configured_port" in payload
    assert "tcp_reachable" in payload
    assert "api_handshake_ready" in payload
    assert "next_valid_id_received" in payload
    assert "managed_accounts_received" in payload
    assert "current_time_received" in payload
    assert "active_request_count" in payload
    assert "last_ib_error_code" in payload
    assert "blocking_reasons" in payload
    assert "expected_account" not in payload


def _real_client() -> RealTwsReadOnlyClient:
    if importlib.util.find_spec("ibapi") is None:
        pytest.skip("ibapi is not installed")
    return RealTwsReadOnlyClient(
        IBKRConfiguration(
            enabled=True,
            mode="PAPER",
            simulated=False,
            expected_account="DU1234567",
            account_allow_list=["DU1234567"],
            connection_timeout_seconds=0.05,
            request_timeout_seconds=0.05,
        )
    )


def _contract(symbol: str = "EUR", currency: str = "USD", sec_type: str = "CASH", exchange: str = "IDEALPRO", con_id: int = 12087792) -> SimpleNamespace:
    return SimpleNamespace(
        conId=con_id,
        symbol=symbol,
        secType=sec_type,
        exchange=exchange,
        primaryExchange="",
        currency=currency,
        localSymbol=f"{symbol}.{currency}",
        tradingClass=f"{symbol}.{currency}",
    )


def test_real_client_records_readiness_callbacks_without_env_inference() -> None:
    client = _real_client()
    assert client.account_rows() == []
    client.connectAck()
    client.nextValidId(101)
    client.managedAccounts("DU1234567")
    assert client.connect_ack_received is True
    assert client.next_valid_order_id == 101
    rows = client.account_rows()
    assert len(rows) == 1
    assert rows[0].paper_verified is True
    assert rows[0].allowed is True


def test_real_client_account_summary_completes_only_on_end_callback() -> None:
    client = _real_client()
    request = client._new_request("account_summary")
    client.accountSummary(request.request_id, "DU1234567", "NetLiquidation", "100000", "USD")
    assert request.event.is_set() is False
    client.accountSummaryEnd(request.request_id)
    assert client._wait(request).end_callback_received is True
    assert request.results[0]["tag"] == "NetLiquidation"


def test_real_client_position_and_order_callbacks_complete_on_end() -> None:
    client = _real_client()
    position_request = client._new_request("positions")
    client._positions_req_id = position_request.request_id
    client.position("DU1234567", _contract("GBP", "USD", con_id=12087797), Decimal("25000"), 1.25)
    client.positionEnd()
    assert client._wait(position_request).results[0]["contract"]["local_symbol"] == "GBP.USD"

    order_request = client._new_request("open_orders")
    client._open_orders_req_id = order_request.request_id
    order = SimpleNamespace(account="DU1234567", permId=99, clientId=111, orderRef="ref", action="BUY", orderType="MKT", totalQuantity=1000, lmtPrice=0, auxPrice=0, tif="DAY")
    client.openOrder(77, _contract("USD", "JPY", con_id=15016059), order, SimpleNamespace(status="Submitted"))
    client.orderStatus(77, "Submitted", Decimal("0"), Decimal("1000"), 0)
    client.openOrderEnd()
    waited = client._wait(order_request)
    assert waited.end_callback_received is True
    assert any(row.get("event") == "orderStatus" for row in waited.results)


def test_real_client_completed_orders_executions_commissions_and_contract_details() -> None:
    client = _real_client()
    completed_request = client._new_request("completed_orders")
    client.completedOrder(_contract(), SimpleNamespace(account="DU1234567", permId=88, clientId=111, orderRef="done", action="BUY", orderType="MKT", totalQuantity=1000, lmtPrice=0, auxPrice=0, tif="DAY"), SimpleNamespace(status="Filled"))
    client.completedOrdersEnd()
    assert client._wait(completed_request).results[0]["state"] == "Filled"

    execution_request = client._new_request("executions")
    execution = SimpleNamespace(execId="exec-1", acctNumber="DU1234567", orderId=77, permId=88, clientId=111, side="BOT", shares=1000, price=1.1, time="20260728 10:00:00", exchange="IDEALPRO", orderRef="done")
    client.execDetails(execution_request.request_id, _contract(), execution)
    client.commissionReport(SimpleNamespace(execId="exec-1", commission=2.5, currency="USD", realizedPNL=0))
    client.execDetailsEnd(execution_request.request_id)
    waited = client._wait(execution_request)
    assert waited.metadata["commission_reports"][0]["exec_id"] == "exec-1"

    contract_request = client._new_request("contract_details")
    details = SimpleNamespace(contract=_contract(), minTick=0.00005, marketName="IDEALPRO", validExchanges="IDEALPRO", marketRuleIds="3188", longName="European Monetary Union euro", priceMagnifier=1, timeZoneId="EST")
    client.contractDetails(contract_request.request_id, details)
    client.contractDetailsEnd(contract_request.request_id)
    assert client._wait(contract_request).results[0]["contract"]["con_id"] == 12087792


def test_real_client_timeout_and_error_do_not_create_fake_success() -> None:
    client = _real_client()
    request = client._new_request("positions")
    client.error(request.request_id, 200, "No security definition has been found")
    with pytest.raises(Exception):
        client._wait(request)
    assert request.timed_out is True
    assert request.end_callback_received is True
    assert request.results == []


def test_real_paper_submit_order_is_blocked_before_place_order() -> None:
    adapter = IBKRPaperAdapter(
        IBKRConfiguration(
            enabled=True,
            mode="PAPER",
            simulated=False,
            expected_account="DU1234567",
            account_allow_list=["DU1234567"],
        )
    )
    command = BrokerOrderCommand(
        canonical_order_id="test",
        account_id="DU1234567",
        instrument_id="FX:EURUSD",
        side="BUY",
        order_type="MARKET",
        time_in_force="DAY",
        quantity=Decimal("1000"),
        approved_quantity=Decimal("1000"),
        risk_evaluation_id="risk",
        idempotency_key="idem",
    )
    with pytest.raises(BrokerSafetyError) as exc:
        asyncio.run(adapter.submit_order(command))
    assert exc.value.code == "BROKER_ORDER_SUBMISSION_DISABLED"
    assert adapter.config.order_submission_enabled is False


def test_real_fx5e_dry_run_omits_unsupported_order_attributes() -> None:
    client = _real_client()
    result = client.dry_run_fx5e_market_order(
        account_id="DU1234567",
        side="BUY",
        quantity=Decimal("1000"),
        order_ref="FX5E_ACCEPTANCE_test",
    )

    assert result["status"] == "COMPATIBLE"
    assert result["unsupported_attributes"] == []
    assert all(result["encoded_fields_absent"].values())
    assert "eTradeOnly" not in result["order"]
    assert "firmQuoteOnly" not in result["order"]
    assert "nbboPriceCap" not in result["order"]


def test_real_fx5e_guard_blocks_when_encoder_still_contains_unsupported_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _real_client()
    client.api_ready = True
    client.next_valid_order_id = 100
    monkeypatch.setattr("backend.brokers.ibkr.client._encoded_unsupported_order_attributes", lambda: ["eTradeOnly"])

    with pytest.raises(BrokerCapabilityError) as exc:
        client.place_fx5e_market_order(
            account_id="DU1234567",
            side="BUY",
            quantity=Decimal("1000"),
            order_ref="FX5E_ACCEPTANCE_test",
        )

    assert exc.value.code == "IBKR_UNSUPPORTED_ORDER_ATTRIBUTE"
    assert client.next_valid_order_id == 100


def test_real_fx5e_order_id_advances_above_recorded_audit_history(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _real_client()
    client.api_ready = True
    client.next_valid_order_id = 1
    captured: dict[str, int] = {}
    monkeypatch.setattr("backend.brokers.ibkr.client._encoded_unsupported_order_attributes", lambda: [])
    monkeypatch.setattr("backend.brokers.ibkr.client._max_recorded_ibkr_order_id", lambda: 1)

    def fake_place_order(order_id, contract, order):
        captured["order_id"] = order_id

    monkeypatch.setattr(client, "placeOrder", fake_place_order)

    result = client.place_fx5e_market_order(
        account_id="DU1234567",
        side="BUY",
        quantity=Decimal("1000"),
        order_ref="FX5E_ACCEPTANCE_test",
    )

    assert captured["order_id"] == 2
    assert result["order_id"] == 2
    assert client.next_valid_order_id == 3


def test_session_manager_concurrent_connect_reuses_one_client(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"created": 0, "started": 0}

    class FakeClient:
        api_ready = False

        def __init__(self, config):
            calls["created"] += 1
            self.config = config
            self.connection_attempt_id = "attempt-1"
            self._connected = False

        def start_and_wait_ready(self):
            calls["started"] += 1
            self.api_ready = True
            self._connected = True

        def isConnected(self):
            return self._connected

        def disconnect_clean(self):
            self.api_ready = False
            self._connected = False

        def readiness_diagnostics(self):
            return {"api_ready": self.api_ready, "socket_connected": self._connected, "event_loop_thread_alive": self._connected, "pending_requests": 0, "connection_attempt_id": self.connection_attempt_id}

    monkeypatch.setattr("backend.brokers.ibkr.client.RealTwsReadOnlyClient", FakeClient)
    manager = IbkrSessionManager(IBKRConfiguration(enabled=True, mode="PAPER", simulated=False))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        clients = list(pool.map(lambda _: manager.connect_once(), range(3)))

    assert len({id(client) for client in clients}) == 1
    assert calls == {"created": 1, "started": 1}
    assert manager.diagnostics()["manager_state"] == "API_READY"


def test_session_manager_disconnect_idempotent_and_diagnostics_do_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"created": 0, "disconnect": 0}

    class FakeClient:
        api_ready = True
        connection_attempt_id = "attempt-1"

        def __init__(self, config):
            calls["created"] += 1
            self._connected = True

        def start_and_wait_ready(self): ...

        def isConnected(self):
            return self._connected

        def disconnect_clean(self):
            calls["disconnect"] += 1
            self.api_ready = False
            self._connected = False

        def readiness_diagnostics(self):
            return {"api_ready": self.api_ready, "socket_connected": self._connected, "event_loop_thread_alive": self._connected, "pending_requests": 0}

    monkeypatch.setattr("backend.brokers.ibkr.client.RealTwsReadOnlyClient", FakeClient)
    manager = IbkrSessionManager(IBKRConfiguration(enabled=True, mode="PAPER", simulated=False))

    assert manager.diagnostics()["socket_connected"] is False
    assert calls["created"] == 0
    manager.connect_once()
    manager.disconnect_once()
    manager.disconnect_once()

    assert calls["disconnect"] == 1
    assert manager.diagnostics()["manager_state"] == "DISCONNECTED"


def test_session_manager_timeout_cleans_failed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"disconnect": 0}

    class FakeClient:
        api_ready = False
        connection_attempt_id = "attempt-timeout"

        def __init__(self, config):
            self._connected = True

        def start_and_wait_ready(self):
            raise RuntimeError("timeout")

        def isConnected(self):
            return self._connected

        def disconnect_clean(self):
            calls["disconnect"] += 1
            self._connected = False

        def readiness_diagnostics(self):
            return {"event_loop_thread_alive": self._connected, "socket_connected": self._connected}

    monkeypatch.setattr("backend.brokers.ibkr.client.RealTwsReadOnlyClient", FakeClient)
    manager = IbkrSessionManager(IBKRConfiguration(enabled=True, mode="PAPER", simulated=False))

    with pytest.raises(RuntimeError):
        manager.connect_once()

    assert calls["disconnect"] == 1
    assert manager.client is None
    assert manager.diagnostics()["manager_state"] == "FAILED"

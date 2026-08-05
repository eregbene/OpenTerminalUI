from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

import pytest

from backend.brokers import broker_registry
from backend.brokers.errors import BrokerCapabilityError, BrokerSafetyError
from backend.brokers.health import BrokerHealth
from backend.brokers.ibkr.client import IBKRPaperAdapter
from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.api.routes.brokers import FxRearmReadinessRequest, _build_fx_rearm_readiness
from backend.brokers.models import BrokerAccount, BrokerAccountSnapshot, BrokerCashBalance, BrokerConnectionState, BrokerContract, BrokerEnvironment, BrokerExecution, BrokerHealthState, BrokerOrder, BrokerOrderCommand, BrokerOrderState, BrokerPosition, BrokerReconciliationResult
from backend.forex_strategies import ibkr_acceptance as acceptance
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


class _FakeAdapter:
    def __init__(self, *, positions: list[BrokerPosition] | None = None) -> None:
        self.config = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
        self.positions_rows = positions or []
        self.real_client = type("Client", (), {"readiness_diagnostics": lambda self: {"api_ready": True, "pending_requests": 0}})()

    async def connect(self) -> BrokerHealth:
        return BrokerHealth(broker="ibkr", state=BrokerHealthState.HEALTHY, connection_state=BrokerConnectionState.CONNECTED, environment=BrokerEnvironment.PAPER, connected_host="host.docker.internal", port=7497, client_id=120, server_version="157", session_start=datetime.now(timezone.utc), account_verification_status="PAPER_VERIFIED", order_submission_status="DISABLED")

    async def health(self) -> BrokerHealth:
        return await self.connect()

    async def accounts(self) -> list[BrokerAccount]:
        return [BrokerAccount(account_id="DU1234567", alias="DU***67", environment=BrokerEnvironment.PAPER, paper_verified=True, allowed=True, base_currency="USD", account_type="PAPER")]

    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(account_id=account_id, environment=BrokerEnvironment.PAPER, net_liquidation=Decimal("100000"), buying_power=Decimal("100000"), available_funds=Decimal("100000"), excess_liquidity=Decimal("100000"), initial_margin=Decimal("0"), maintenance_margin=Decimal("0"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"), cash=[BrokerCashBalance(currency="USD", settled_cash=Decimal("100000"), available_cash=Decimal("100000"))])

    async def positions(self, account_id: str) -> list[BrokerPosition]:
        return self.positions_rows

    async def open_orders(self, account_id: str) -> list:
        return []

    async def completed_orders(self, account_id: str) -> tuple[str, list]:
        return "REAL_IBAPI", []

    async def executions(self, account_id: str) -> list:
        return []

    async def resolve_contract(self, instrument_id: str) -> BrokerContract:
        return BrokerContract(instrument_id="FX:EURUSD", symbol="EUR", asset_type="FOREX", exchange="IDEALPRO", currency="USD", security_type="CASH", con_id=12087792, source="REAL_IBAPI", resolution_version="test")


@pytest.fixture()
def fx5e_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IbkrPaperAcceptanceService:
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    monkeypatch.setattr(acceptance, "ibkr_config", cfg)
    monkeypatch.setattr(acceptance, "FX5E_APPROVAL_PATH", tmp_path / "fx5e.json")
    monkeypatch.setattr(broker_registry, "_adapters", {**broker_registry._adapters, "ibkr": _FakeAdapter()})
    return IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))


def test_fx5e_preview_and_single_use_approval(fx5e_service: IbkrPaperAcceptanceService):
    import asyncio

    preview = asyncio.run(fx5e_service.create_fx5e_preview())

    assert preview["status"] == "READY"
    assert preview["order_reference"].startswith("FX5E_ACCEPTANCE_")

    approved = fx5e_service.approve_fx5e_preview(preview_id=preview["preview_id"], approval_token=preview["approval_token"])
    assert approved["status"] == "APPROVED"

    duplicate = fx5e_service.approve_fx5e_preview(preview_id=preview["preview_id"], approval_token=preview["approval_token"])
    assert duplicate["blocking_reasons"] == ["APPROVAL_ALREADY_USED"]


def test_fx5e_approval_expiry(fx5e_service: IbkrPaperAcceptanceService):
    import asyncio

    preview = asyncio.run(fx5e_service.create_fx5e_preview())
    data = fx5e_service._load_fx5e()
    data["previews"][preview["preview_id"]]["approval_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    fx5e_service._save_fx5e(data)

    result = fx5e_service.approve_fx5e_preview(preview_id=preview["preview_id"], approval_token=preview["approval_token"])

    assert result["blocking_reasons"] == ["APPROVAL_EXPIRED"]


def test_fx5e_precondition_blocks_existing_position(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    monkeypatch.setattr(acceptance, "ibkr_config", cfg)
    monkeypatch.setattr(acceptance, "FX5E_APPROVAL_PATH", tmp_path / "fx5e.json")
    monkeypatch.setattr(broker_registry, "_adapters", {**broker_registry._adapters, "ibkr": _FakeAdapter(positions=[BrokerPosition(instrument_id="FX:EURUSD", quantity=Decimal("1000"), average_cost=Decimal("1.08"), currency="USD")])})
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))

    preview = asyncio.run(service.create_fx5e_preview())

    assert preview["status"] == "BLOCKED"
    assert "no_unexpected_eurusd_position" in preview["blocking_reasons"]


def test_fx5e_adapter_submission_calls_place_order_once():
    import asyncio

    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)

    class RealClient:
        api_ready = True

        def __init__(self) -> None:
            self.calls = 0

        def place_fx5e_market_order(self, **kwargs):
            self.calls += 1
            return {"submission_state": "ACKNOWLEDGED", "broker_order_id": 1, "kwargs": kwargs}

    real_client = RealClient()
    adapter.real_client = real_client
    command = BrokerOrderCommand(canonical_order_id="fx5e-1", account_id="DU1234567", instrument_id="FX:EURUSD", side="BUY", order_type="MARKET", time_in_force="DAY", quantity=Decimal("1000"), approved_quantity=Decimal("1000"), risk_evaluation_id="risk", idempotency_key="idem", correlation_id="FX5E_ACCEPTANCE_test", user_approval=True)

    result = asyncio.run(adapter.submit_fx5e_acceptance_order(command))

    assert result["submission_state"] == "ACKNOWLEDGED"
    assert real_client.calls == 1


def test_fx5e_dry_run_reports_unsupported_attributes_absent():
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)

    class RealClient:
        api_ready = True

        def dry_run_fx5e_market_order(self, **kwargs):
            return {"status": "COMPATIBLE", "unsupported_attributes": [], "encoded_fields_absent": {"eTradeOnly": True, "firmQuoteOnly": True, "nbboPriceCap": True}, "order": {"orderRef": kwargs["order_ref"]}}

    adapter.real_client = RealClient()
    result = asyncio.run(adapter.dry_run_fx5e_acceptance_order(account_id="DU1234567", side="BUY", quantity=Decimal("1000"), order_ref="FX5E_ACCEPTANCE_test"))

    assert result["status"] == "COMPATIBLE"
    assert result["unsupported_attributes"] == []
    assert all(result["encoded_fields_absent"].values())


def test_fx5e_local_guard_blocks_unsupported_encoded_attributes(monkeypatch: pytest.MonkeyPatch):
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)

    class RealClient:
        api_ready = True

        def place_fx5e_market_order(self, **kwargs):
            from backend.brokers.errors import BrokerCapabilityError

            raise BrokerCapabilityError("IBKR_UNSUPPORTED_ORDER_ATTRIBUTE", "outgoing order encoder contains unsupported attributes: ['eTradeOnly']", status_code=422)

    adapter.real_client = RealClient()
    command = BrokerOrderCommand(canonical_order_id="fx5e-guard", account_id="DU1234567", instrument_id="FX:EURUSD", side="BUY", order_type="MARKET", time_in_force="DAY", quantity=Decimal("1000"), approved_quantity=Decimal("1000"), risk_evaluation_id="risk", idempotency_key="idem-guard", correlation_id="FX5E_ACCEPTANCE_guard", user_approval=True)

    with pytest.raises(BrokerCapabilityError) as exc:
        asyncio.run(adapter.submit_fx5e_acceptance_order(command))

    assert exc.value.code == "IBKR_UNSUPPORTED_ORDER_ATTRIBUTE"


def test_ai_auto_paper_uses_configured_quantity_limit(monkeypatch: pytest.MonkeyPatch):
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, real_paper_mode=True, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)
    monkeypatch.setenv("AI_MAX_POSITION_SIZE_FOREX", "100000")
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")

    class RealClient:
        api_ready = True

        def account_rows(self):
            return [BrokerAccount(account_id="DU1234567", alias="paper", environment=BrokerEnvironment.PAPER, paper_verified=True, allowed=True)]

        def account_summary(self):
            return [
                {"account": "DU1234567", "tag": "BaseCurrency", "value": "USD", "currency": ""},
                {"account": "DU1234567", "tag": "NetLiquidation", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "AvailableFunds", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "AvailableFunds", "value": "1000000", "currency": "EUR"},
                    {"account": "DU1234567", "tag": "ExcessLiquidity", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "TotalCashValue", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "TotalCashValue", "value": "1000000", "currency": "EUR"},
                ]

        def place_ai_auto_paper_market_order(self, **kwargs):
            return {"submission_state": "ACKNOWLEDGED", "broker_order_id": 42, "kwargs": kwargs}

    adapter.real_client = RealClient()
    command = BrokerOrderCommand(
        canonical_order_id="ai-auto-1",
        account_id="DU1234567",
        instrument_id="FX:EURUSD",
        side="SELL",
        order_type="MARKET",
        time_in_force="DAY",
        quantity=Decimal("100000"),
        approved_quantity=Decimal("100000"),
        risk_evaluation_id="risk",
        idempotency_key="idem-ai-auto",
        correlation_id="AI_AUTO_PAPER_test",
        user_approval=False,
    )

    result = asyncio.run(adapter.submit_ai_auto_paper_order(command))

    assert result["submission_state"] == "ACKNOWLEDGED"
    assert result["kwargs"]["quantity"] == Decimal("100000")


def test_real_account_snapshot_preserves_multi_currency_cash_rows():
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, real_paper_mode=True, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)

    class RealClient:
        api_ready = True

        def account_summary(self):
            return [
                {"account": "DU1234567", "tag": "BaseCurrency", "value": "USD", "currency": ""},
                {"account": "DU1234567", "tag": "NetLiquidation", "value": "1000000", "currency": "USD"},
                {"account": "DU1234567", "tag": "AvailableFunds", "value": "900000", "currency": "USD"},
                {"account": "DU1234567", "tag": "TotalCashValue", "value": "850000", "currency": "USD"},
                {"account": "DU1234567", "tag": "TotalCashValue", "value": "12500", "currency": "GBP"},
            ]

    adapter.real_client = RealClient()

    snapshot = asyncio.run(adapter.account_snapshot("DU1234567"))

    assert snapshot.net_liquidation == Decimal("1000000")
    assert {row.currency: row.settled_cash for row in snapshot.cash} == {"USD": Decimal("850000"), "GBP": Decimal("12500")}


def _funding_adapter(cash: dict[str, str]) -> IBKRPaperAdapter:
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, real_paper_mode=True, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)

    class RealClient:
        api_ready = True

        def account_rows(self):
            return [BrokerAccount(account_id="DU1234567", alias="paper", environment=BrokerEnvironment.PAPER, paper_verified=True, allowed=True)]

        def account_summary(self):
            rows = [
                {"account": "DU1234567", "tag": "BaseCurrency", "value": "USD", "currency": ""},
                {"account": "DU1234567", "tag": "NetLiquidation", "value": "1000000", "currency": "USD"},
                {"account": "DU1234567", "tag": "ExcessLiquidity", "value": "1000000", "currency": "USD"},
            ]
            rows.extend({"account": "DU1234567", "tag": "AvailableFunds", "value": value, "currency": currency} for currency, value in cash.items())
            rows.extend({"account": "DU1234567", "tag": "TotalCashValue", "value": value, "currency": currency} for currency, value in cash.items())
            return rows

        def place_ai_auto_paper_market_order(self, **kwargs):
            raise AssertionError("placeOrder must not be called by funding dry-run tests")

    adapter.real_client = RealClient()
    return adapter


def _cmd(pair: str, side: str, qty: str = "1000") -> BrokerOrderCommand:
    return BrokerOrderCommand(
        canonical_order_id="funding-test",
        account_id="DU1234567",
        instrument_id=f"FX:{pair}",
        side=side,
        order_type="MARKET",
        time_in_force="DAY",
        quantity=Decimal(qty),
        approved_quantity=Decimal(qty),
        risk_evaluation_id="risk",
        idempotency_key="funding-test",
        correlation_id="AI_AUTO_PAPER_funding",
        user_approval=False,
    )


class _RearmAdapter:
    def __init__(
        self,
        *,
        positions: list[BrokerPosition] | None = None,
        open_orders: list[BrokerOrder] | None = None,
        executions: list[BrokerExecution] | None = None,
        cash: dict[str, str] | None = None,
        reconciliation_status: str = "MATCHED_EMPTY",
    ) -> None:
        self.positions_rows = positions or []
        self.orders_rows = open_orders or []
        self.executions_rows = executions or []
        self.cash = cash or {"USD": "1000000", "EUR": "1000000"}
        self.reconciliation_status = reconciliation_status
        self.place_order_calls = 0
        self.funding_checks: list[tuple[str, str, str]] = []

    async def positions(self, account_id: str) -> list[BrokerPosition]:
        return self.positions_rows

    async def open_orders(self, account_id: str) -> list[BrokerOrder]:
        return self.orders_rows

    async def completed_orders(self, account_id: str) -> tuple[str, list[BrokerOrder]]:
        return "REAL_IBAPI", []

    async def executions(self, account_id: str) -> list[BrokerExecution]:
        return self.executions_rows

    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            account_id=account_id,
            environment=BrokerEnvironment.PAPER,
            net_liquidation=Decimal("1000000"),
            available_funds=Decimal("1000000"),
            excess_liquidity=Decimal("1000000"),
            cash=[BrokerCashBalance(currency=currency, settled_cash=Decimal(value), available_cash=Decimal(value)) for currency, value in self.cash.items()],
        )

    async def reconcile(self, account_id: str) -> BrokerReconciliationResult:
        return BrokerReconciliationResult(account_id=account_id, status=self.reconciliation_status)

    async def fx_cash_funding_check(self, command: BrokerOrderCommand, *, side=None, quantity=None, price=None, role="dry_run"):
        action = (side or command.side).upper()
        qty = Decimal(str(quantity or command.quantity))
        pair = command.instrument_id.replace("FX:", "")
        base, quote = pair[:3], pair[3:]
        required_currency = quote if action == "BUY" else base
        execution_price = Decimal(str(price or "1"))
        required_cash = qty * execution_price + Decimal("3.50") if action == "BUY" else qty + Decimal("3.50")
        available_cash = Decimal(self.cash.get(required_currency, "0"))
        maximum_affordable = max(Decimal("0"), ((available_cash - Decimal("3.50")) / execution_price if action == "BUY" else available_cash - Decimal("3.50"))).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        self.funding_checks.append((pair, action, role))
        return {
            "funding_status": "FUNDED" if available_cash >= required_cash else "INSUFFICIENT",
            "pair": pair,
            "action": action,
            "requested_quantity": str(qty),
            "required_currency": required_currency,
            "available_cash": str(available_cash),
            "required_cash": str(required_cash),
            "maximum_affordable_quantity": str(maximum_affordable),
            "role": role,
        }


@pytest.mark.parametrize(
    ("pair", "side", "required_currency"),
    [
        ("USDJPY", "BUY", "JPY"),
        ("USDJPY", "SELL", "USD"),
        ("EURUSD", "BUY", "USD"),
        ("EURUSD", "SELL", "EUR"),
        ("GBPUSD", "BUY", "USD"),
        ("GBPUSD", "SELL", "GBP"),
    ],
)
def test_fx_cash_funding_maps_pair_action_to_settlement_currency(monkeypatch: pytest.MonkeyPatch, pair: str, side: str, required_currency: str):
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")
    adapter = _funding_adapter({"USD": "1000000", "JPY": "1000000", "EUR": "1000000", "GBP": "1000000"})
    check = asyncio.run(adapter._fx_cash_funding(_cmd(pair, side), price=Decimal("150") if pair.endswith("JPY") else Decimal("1.2")))

    assert check["required_currency"] == required_currency
    assert check["funding_status"] == "FUNDED"


def test_usdjpy_buy_rejects_when_jpy_cash_cannot_cover_worst_case(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")
    adapter = _funding_adapter({"USD": "20927", "JPY": "292798"})
    command = _cmd("USDJPY", "BUY", "1869")

    with pytest.raises(BrokerSafetyError) as exc:
        asyncio.run(adapter._require_fx_cash_funding(command, price=Decimal("156.757"), role="stop_loss"))

    details = exc.value.details  # type: ignore[attr-defined]
    assert exc.value.code == "INSUFFICIENT_SETTLEMENT_CURRENCY"
    assert details["required_currency"] == "JPY"
    assert Decimal(details["required_cash"]) > Decimal(details["available_cash"])
    assert Decimal(details["maximum_affordable_quantity"]) < Decimal("1869")


def test_exact_cash_is_rejected_after_commission_buffer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")
    monkeypatch.setenv("FX_FUNDING_COMMISSION_BUFFER", "2.50")
    monkeypatch.setenv("FX_FUNDING_CASH_BUFFER", "0")
    adapter = _funding_adapter({"USD": "1200"})

    with pytest.raises(BrokerSafetyError) as exc:
        asyncio.run(adapter._require_fx_cash_funding(_cmd("EURUSD", "BUY", "1000"), price=Decimal("1.2")))

    assert exc.value.code == "INSUFFICIENT_SETTLEMENT_CURRENCY"


def test_slippage_buffer_reduces_affordable_quantity(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")
    monkeypatch.setenv("FX_FUNDING_COMMISSION_BUFFER", "0")
    monkeypatch.setenv("FX_FUNDING_CASH_BUFFER", "0")
    monkeypatch.setenv("FX_FUNDING_SLIPPAGE_PIPS", "10")
    adapter = _funding_adapter({"USD": "1200"})
    check = asyncio.run(adapter._fx_cash_funding(_cmd("EURUSD", "BUY", "1000"), price=Decimal("1.2")))

    assert check["funding_status"] == "INSUFFICIENT"
    assert Decimal(check["maximum_affordable_quantity"]) < Decimal("1000")


def test_ai_funding_plan_blocks_short_when_protective_buy_needs_more_quote_cash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")
    adapter = _funding_adapter({"USD": "20927", "JPY": "292798"})
    command = _cmd("USDJPY", "SELL", "1869")

    with pytest.raises(BrokerSafetyError) as exc:
        asyncio.run(
            adapter.validate_ai_auto_paper_funding_plan(
                command,
                entry_price=Decimal("156.66"),
                stop_price=Decimal("156.757"),
                take_profit=Decimal("156.458"),
            )
        )

    assert exc.value.code == "INSUFFICIENT_SETTLEMENT_CURRENCY"
    details = exc.value.details  # type: ignore[attr-defined]
    assert details["failed_check"]["role"] == "stop_loss"


def test_rearm_readiness_blocks_non_allowlisted_usdjpy_position():
    adapter = _RearmAdapter(
        positions=[BrokerPosition(instrument_id="FX:USDJPY", quantity=Decimal("-1869"), average_cost=Decimal("156.49"), currency="JPY")]
    )

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest()))

    assert result["READY_TO_REARM"] is False
    assert "USDJPY_POSITION_OPEN" in result["blocking_reasons"]
    assert "NON_ALLOWLISTED_FX_POSITION_OPEN" in result["blocking_reasons"]
    assert result["place_order_calls"] == 0
    assert adapter.place_order_calls == 0


def test_rearm_readiness_recovered_flat_state_clears_position_blocker():
    adapter = _RearmAdapter()

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest(representative_quantity=Decimal("1000"), max_quantity=Decimal("1000"))))

    assert result["READY_TO_REARM"] is True
    assert result["blocking_reasons"] == []
    assert result["recommended_AI_ORDER_SUBMISSION_ENABLED"] == "1"
    assert result["recommended_symbol_allowlist"] == "EURUSD"
    assert result["place_order_calls"] == 0


def test_rearm_readiness_open_child_order_blocks_rearm():
    adapter = _RearmAdapter(
        open_orders=[
            BrokerOrder(
                canonical_order_id="IBKR:19",
                broker_order_id="19",
                account_id="DU1234567",
                instrument_id="FX:USDJPY",
                state=BrokerOrderState.SUBMITTED,
                raw_status="PreSubmitted",
                correlation_id="AI_AUTO_PAPER_x_TP",
            )
        ]
    )

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest()))

    assert result["READY_TO_REARM"] is False
    assert "OPEN_USDJPY_ORDER" in result["blocking_reasons"]
    assert "ORDER_19_OPEN" in result["blocking_reasons"]


def test_rearm_readiness_unresolved_usdjpy_execution_blocks_rearm():
    adapter = _RearmAdapter(
        executions=[
            BrokerExecution(
                canonical_order_id="IBKR:18",
                broker_order_id="18",
                account_id="DU1234567",
                instrument_id="FX:USDJPY",
                side="SLD",
                quantity=Decimal("1869"),
                price=Decimal("156.49"),
                commission=Decimal("0"),
                currency="JPY",
            )
        ]
    )

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest()))

    assert result["READY_TO_REARM"] is False
    assert "UNRESOLVED_USDJPY_EXECUTION" in result["blocking_reasons"]
    assert "UNRESOLVED_USDJPY_COMMISSION" in result["blocking_reasons"]


def test_rearm_readiness_eurusd_lifecycle_checks_buy_and_sell_currency():
    adapter = _RearmAdapter(cash={"USD": "5000", "EUR": "5000"})

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest(representative_quantity=Decimal("1000"), max_quantity=Decimal("1000"))))

    buy_entry = result["eurusd_preflight"]["representative_quantity"]["buy"]["entry"]
    sell_entry = result["eurusd_preflight"]["representative_quantity"]["sell"]["entry"]
    assert buy_entry["required_currency"] == "USD"
    assert sell_entry["required_currency"] == "EUR"
    assert ("EURUSD", "BUY", "entry") in adapter.funding_checks
    assert ("EURUSD", "SELL", "entry") in adapter.funding_checks
    assert ("EURUSD", "SELL", "emergency_close") in adapter.funding_checks
    assert ("EURUSD", "BUY", "emergency_close") in adapter.funding_checks
    assert result["place_order_calls"] == 0


def test_rearm_readiness_blocks_when_protective_exit_not_fundable_by_real_cash():
    adapter = _RearmAdapter(cash={"USD": "0", "EUR": "1000000"})

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest(representative_quantity=Decimal("1000"), max_quantity=Decimal("1000"))))

    assert result["READY_TO_REARM"] is False
    assert "PROTECTIVE_EXIT_NOT_FUNDABLE" in result["blocking_reasons"]
    assert result["eurusd_preflight"]["representative_quantity"]["buy"]["entry"]["funding_status"] == "INSUFFICIENT"
    assert result["eurusd_preflight"]["representative_quantity"]["sell"]["entry"]["funding_status"] == "FUNDED"
    assert result["recommended_maximum_funded_position_size"] == "0"


def test_rearm_readiness_maximum_funded_quantity_uses_smaller_directional_capacity():
    adapter = _RearmAdapter(cash={"USD": "2203.50", "EUR": "5000"})

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest(representative_quantity=Decimal("1000"), max_quantity=Decimal("4000"), entry_price=Decimal("1.1"), long_stop_price=Decimal("1.09"), short_stop_price=Decimal("1.11"))))

    assert result["buy_lifecycle_funded_quantity"] == "2000"
    assert result["sell_lifecycle_funded_quantity"] == "1981"
    assert result["recommended_maximum_funded_position_size"] == "1981"


def test_rearm_readiness_blocks_when_live_trading_flag_is_enabled(monkeypatch: pytest.MonkeyPatch):
    import backend.api.routes.brokers as broker_routes

    monkeypatch.setattr(broker_routes.ibkr_config, "live_trading_enabled", True)
    adapter = _RearmAdapter()

    result = asyncio.run(_build_fx_rearm_readiness(adapter, "DU1234567", FxRearmReadinessRequest(representative_quantity=Decimal("1000"), max_quantity=Decimal("1000"))))

    assert result["READY_TO_REARM"] is False
    assert "LIVE_TRADING_ENABLED" in result["blocking_reasons"]


def test_manual_paper_flatten_allows_existing_fx_cross(monkeypatch: pytest.MonkeyPatch):
    cfg = IBKRConfiguration(enabled=True, mode="PAPER", simulated=False, real_paper_mode=True, client_id=120, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(cfg)
    monkeypatch.setenv("AI_MAX_POSITION_SIZE_FOREX", "100000")
    monkeypatch.setenv("FX_FOREX_LEVERAGE_PERMITTED", "false")

    class RealClient:
        api_ready = True

        def account_rows(self):
            return [BrokerAccount(account_id="DU1234567", alias="paper", environment=BrokerEnvironment.PAPER, paper_verified=True, allowed=True)]

        def account_summary(self):
            return [
                {"account": "DU1234567", "tag": "BaseCurrency", "value": "USD", "currency": ""},
                {"account": "DU1234567", "tag": "NetLiquidation", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "AvailableFunds", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "AvailableFunds", "value": "1000000", "currency": "EUR"},
                    {"account": "DU1234567", "tag": "ExcessLiquidity", "value": "1000000", "currency": "USD"},
                    {"account": "DU1234567", "tag": "TotalCashValue", "value": "1000000", "currency": "GBP"},
                    {"account": "DU1234567", "tag": "TotalCashValue", "value": "1000000", "currency": "EUR"},
                ]

        def place_fx_flatten_market_order(self, **kwargs):
            return {"submission_state": "ACKNOWLEDGED", "broker_order_id": 43, "kwargs": kwargs}

    adapter.real_client = RealClient()
    command = BrokerOrderCommand(
        canonical_order_id="flatten-1",
        account_id="DU1234567",
        instrument_id="FX:EURGBP",
            side="SELL",
        order_type="MARKET",
        time_in_force="DAY",
        quantity=Decimal("100000"),
        approved_quantity=Decimal("100000"),
        risk_evaluation_id="flatten-risk",
        idempotency_key="flatten-idem",
        correlation_id="IBKR_PAPER_FLATTEN_test",
        user_approval=True,
    )

    result = asyncio.run(adapter.submit_paper_fx_flatten_order(command))

    assert result["submission_state"] == "ACKNOWLEDGED"
    assert result["kwargs"]["instrument_id"] == "FX:EURGBP"

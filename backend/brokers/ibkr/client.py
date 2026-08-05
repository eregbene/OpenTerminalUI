from __future__ import annotations

import importlib.util
import asyncio
import inspect
import threading
import textwrap
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
import os
from typing import Any

from backend.brokers.capabilities import BrokerCapabilities
from backend.brokers.errors import BrokerCapabilityError, BrokerSafetyError
from backend.brokers.health import BrokerHealth
from backend.brokers.ibkr.accounts import discover_accounts, snapshot
from backend.brokers.ibkr.configuration import IBKRConfiguration, ibkr_config
from backend.brokers.ibkr.connection import new_session, verify_paper
from backend.brokers.ibkr.contracts import resolve_contract
from backend.brokers.ibkr.historical import historical_bars
from backend.brokers.ibkr.market_data import quote_for
from backend.brokers.ibkr.orders import order_book
from backend.brokers.ibkr.portfolio import positions as broker_positions
from backend.brokers.ibkr.reconciliation import reconcile as reconcile_broker
from backend.brokers.models import (
    BrokerAccount,
    BrokerAccountSnapshot,
    BrokerBar,
    BrokerCancelCommand,
    BrokerCancelReceipt,
    BrokerCashBalance,
    BrokerContract,
    BrokerExecution,
    BrokerEnvironment,
    BrokerOrder,
    BrokerOrderCommand,
    BrokerOrderReceipt,
    BrokerOrderState,
    BrokerPosition,
    BrokerQuote,
    BrokerReconciliationResult,
)

IBKR_UNSUPPORTED_ORDER_ATTRIBUTES = {"eTradeOnly", "firmQuoteOnly", "nbboPriceCap"}

try:  # pragma: no cover - exercised in Docker/TWS integration when ibapi exists.
    from ibapi.client import EClient
    from ibapi.common import TickerId
    from ibapi.contract import Contract, ContractDetails
    from ibapi.execution import Execution, ExecutionFilter
    from ibapi.order import Order
    from ibapi.order_state import OrderState
    from ibapi.wrapper import EWrapper
except Exception:  # pragma: no cover - local envs may not have ibapi yet.
    EClient = object  # type: ignore[assignment,misc]
    EWrapper = object  # type: ignore[assignment,misc]
    Contract = object  # type: ignore[assignment,misc]
    ContractDetails = object  # type: ignore[assignment,misc]
    Execution = object  # type: ignore[assignment,misc]
    ExecutionFilter = object  # type: ignore[assignment,misc]
    Order = object  # type: ignore[assignment,misc]
    OrderState = object  # type: ignore[assignment,misc]
    TickerId = int  # type: ignore[assignment]


def _patch_ibapi_place_order_unsupported_fields() -> None:
    if not hasattr(EClient, "placeOrder") or getattr(EClient, "_bensim_place_order_compat_patched", False):
        return
    source = inspect.getsource(EClient.placeOrder)
    replacements = {
        "                make_field( order.eTradeOnly),\n": "                make_field( \"\"),\n",
        "                make_field( order.firmQuoteOnly),\n": "                make_field( \"\"),\n",
        "                make_field_handle_empty( order.nbboPriceCap),\n": "                make_field( \"\"),\n",
    }
    patched = source
    for old, new in replacements.items():
        patched = patched.replace(old, new)
    if patched == source:
        return
    namespace = EClient.placeOrder.__globals__
    exec(textwrap.dedent(patched), namespace)
    EClient.placeOrder = namespace["placeOrder"]  # type: ignore[method-assign]
    EClient._bensim_place_order_compat_patched = True  # type: ignore[attr-defined]
    EClient._bensim_blank_order_attrs = tuple(sorted(IBKR_UNSUPPORTED_ORDER_ATTRIBUTES))  # type: ignore[attr-defined]


_patch_ibapi_place_order_unsupported_fields()


def _mask_account(account_id: str | None) -> str:
    if not account_id:
        return ""
    return f"{account_id[:2]}***{account_id[-2:]}" if len(account_id) > 4 else "***"


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        if value in {None, "", "None"}:
            return Decimal(default)
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return Decimal(default)


def _encoded_unsupported_order_attributes() -> list[str]:
    if not hasattr(EClient, "placeOrder"):
        return []
    if getattr(EClient, "_bensim_place_order_compat_patched", False):
        return []
    try:
        source = inspect.getsource(EClient.placeOrder)
    except OSError:
        return sorted(IBKR_UNSUPPORTED_ORDER_ATTRIBUTES)
    return sorted(attr for attr in IBKR_UNSUPPORTED_ORDER_ATTRIBUTES if attr in source)


def _max_recorded_ibkr_order_id() -> int | None:
    try:
        from sqlalchemy import select

        from backend.brokers.ibkr.orm import BrokerOrderORM
        from backend.shared.db import SessionLocal
    except Exception:
        return None
    with SessionLocal() as session:
        rows = session.scalars(
            select(BrokerOrderORM.broker_order_id).where(
                BrokerOrderORM.broker == "IBKR",
                BrokerOrderORM.environment == "PAPER",
                BrokerOrderORM.record_source == "REAL_IBKR",
                BrokerOrderORM.broker_order_id.is_not(None),
                BrokerOrderORM.broker_order_id != "",
            )
        ).all()
    numeric_ids: list[int] = []
    for row in rows:
        try:
            numeric_ids.append(int(str(row)))
        except (TypeError, ValueError):
            continue
    return max(numeric_ids) if numeric_ids else None


def _masked(value: str | None) -> str | None:
    return _mask_account(value) if value else None


class _IbRequest:
    def __init__(self, request_id: int, request_type: str, timeout_seconds: float) -> None:
        self.request_id = request_id
        self.request_type = request_type
        self.started_at = time.time()
        self.timeout_seconds = timeout_seconds
        self.results: list[Any] = []
        self.metadata: dict[str, Any] = {}
        self.errors: list[dict[str, Any]] = []
        self.event = threading.Event()
        self.completed = False
        self.timed_out = False
        self.end_callback_received = False
        self.lock = threading.Lock()

    def add(self, value: Any) -> None:
        with self.lock:
            if not self.completed:
                self.results.append(value)

    def add_error(self, code: int, message: str) -> None:
        with self.lock:
            self.errors.append({"code": code, "message": message, "timestamp": datetime.utcnow().isoformat()})

    def complete(self) -> None:
        with self.lock:
            if self.completed:
                return
            self.completed = True
            self.end_callback_received = True
            self.event.set()


class RealTwsReadOnlyClient(EWrapper, EClient):  # type: ignore[misc,valid-type]
    """Small synchronous facade over ibapi callbacks for read-only broker requests."""

    def __init__(self, config: IBKRConfiguration) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self.config = config
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._next_req_id = 9000
        self._requests: dict[int, _IbRequest] = {}
        self._current_time_req_id: int | None = None
        self._positions_req_id: int | None = None
        self._open_orders_req_id: int | None = None
        self._submitted_orders: dict[int, dict[str, Any]] = {}
        self.next_valid_order_id: int | None = None
        self.managed_accounts: list[str] = []
        self.last_server_time: datetime | None = None
        self.last_error: dict[str, Any] | None = None
        self.connect_ack_received = False
        self.api_ready = False
        self.connection_attempt_id = f"ibkr_ready_{int(time.time() * 1000)}"
        self.socket_connected = False
        self.last_disconnect_reason: str | None = None
        self.readiness_timestamps: dict[str, str] = {}
        self._next_valid_event = threading.Event()
        self._managed_accounts_event = threading.Event()
        self._connection_closed_event = threading.Event()
        self._connect_error_event = threading.Event()

    @property
    def active_request_count(self) -> int:
        return len([row for row in self._requests.values() if not row.completed])

    def start_and_wait_ready(self) -> None:
        if not importlib.util.find_spec("ibapi"):
            raise BrokerCapabilityError("REAL_IBKR_LIBRARY_UNAVAILABLE", "ibapi is not installed", status_code=503)
        with self._lock:
            if self.isConnected() and self.api_ready:
                return
            self.connection_attempt_id = f"ibkr_ready_{int(time.time() * 1000)}"
            self.last_disconnect_reason = None
            self.readiness_timestamps = {"attempt_started_at": self._stage_timestamp()}
            self.connect_ack_received = False
            self.api_ready = False
            self.socket_connected = False
            self.next_valid_order_id = None
            self.managed_accounts = []
            self.last_server_time = None
            self._next_valid_event.clear()
            self._managed_accounts_event.clear()
            self._connection_closed_event.clear()
            self._connect_error_event.clear()
            self.connect(self.config.host, self.config.port, self.config.client_id)
            self.socket_connected = bool(self.isConnected())
            if self.socket_connected:
                self.readiness_timestamps["socket_connected_at"] = self._stage_timestamp()
            self._thread = threading.Thread(target=self.run, name="ibkr-tws-readonly", daemon=True)
            self._thread.start()
            self.readiness_timestamps["event_loop_started_at"] = self._stage_timestamp()
        timeout = self.config.connection_timeout_seconds
        deadline = time.time() + timeout
        while not self._next_valid_event.is_set() and time.time() < deadline:
            if self._connect_error_event.wait(0.05):
                break
            if self._connection_closed_event.is_set() and not self._next_valid_event.is_set():
                break
        if not self._next_valid_event.is_set():
            code = self.last_error.get("code") if self.last_error else None
            reason = "IBKR_CLIENT_ID_CONFLICT" if code == 326 else "IBKR_NEXT_VALID_ID_TIMEOUT"
            self.last_disconnect_reason = reason
            self.readiness_timestamps["next_valid_timeout_at"] = self._stage_timestamp()
            diagnostics = self.readiness_diagnostics()
            self.disconnect_clean()
            raise BrokerCapabilityError(reason, f"nextValidId was not received from TWS; readiness={diagnostics}", status_code=504 if code != 326 else 409)
        if not self._managed_accounts_event.wait(timeout):
            self.last_disconnect_reason = "IBKR_MANAGED_ACCOUNTS_TIMEOUT"
            self.readiness_timestamps["managed_accounts_timeout_at"] = self._stage_timestamp()
            diagnostics = self.readiness_diagnostics()
            self.disconnect_clean()
            raise BrokerCapabilityError("IBKR_MANAGED_ACCOUNTS_TIMEOUT", f"managedAccounts was not received from TWS; readiness={diagnostics}", status_code=504)
        self.current_time()
        self.api_ready = True
        self.readiness_timestamps["api_ready_at"] = self._stage_timestamp()

    def disconnect_clean(self) -> None:
        self.last_disconnect_reason = self.last_disconnect_reason or "CLIENT_DISCONNECT"
        for row in list(self._requests.values()):
            row.complete()
        if self.isConnected():
            self.disconnect()
        self._connection_closed_event.wait(timeout=1)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self.socket_connected = False
        self.api_ready = False
        self._next_valid_event.clear()
        self._managed_accounts_event.clear()
        self._connect_error_event.clear()
        self._requests.clear()
        self.readiness_timestamps["disconnected_at"] = self._stage_timestamp()

    def readiness_diagnostics(self) -> dict[str, Any]:
        thread = self._thread
        return {
            "socket_connected": bool(self.socket_connected or self.isConnected()),
            "event_loop_thread_alive": bool(thread and thread.is_alive()),
            "process_id": os.getpid(),
            "thread_id": threading.get_ident(),
            "connect_ack_received": self.connect_ack_received,
            "next_valid_id_received": self.next_valid_order_id is not None,
            "managed_accounts_received": bool(self.managed_accounts),
            "current_time_received": self.last_server_time is not None,
            "api_ready": self.api_ready,
            "client_id": self.config.client_id,
            "connection_attempt_id": self.connection_attempt_id,
            "last_error_code": self.last_error.get("code") if self.last_error else None,
            "last_disconnect_reason": self.last_disconnect_reason,
            "pending_requests": self.active_request_count,
            "timestamps": dict(self.readiness_timestamps),
        }

    @staticmethod
    def _stage_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_request(self, request_type: str) -> _IbRequest:
        with self._lock:
            self._next_req_id += 1
            request = _IbRequest(self._next_req_id, request_type, self.config.request_timeout_seconds)
            self._requests[request.request_id] = request
            return request

    def _wait(self, request: _IbRequest) -> _IbRequest:
        if not request.event.wait(request.timeout_seconds):
            request.timed_out = True
            request.add_error(504, f"{request.request_type} timed out")
            request.complete()
            raise BrokerCapabilityError("IBKR_REQUEST_TIMEOUT", f"{request.request_type} timed out", status_code=504)
        failures = [err for err in request.errors if int(err.get("code", 0)) not in {2104, 2106, 2158, 2108}]
        if failures and not request.results:
            raise BrokerCapabilityError("IBKR_REQUEST_FAILED", failures[-1]["message"], status_code=502)
        return request

    def connectAck(self) -> None:  # noqa: N802
        self.connect_ack_received = True
        self.readiness_timestamps["connect_ack_at"] = self._stage_timestamp()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.next_valid_order_id = orderId
        self.readiness_timestamps["next_valid_id_at"] = self._stage_timestamp()
        self._next_valid_event.set()

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        self.managed_accounts = [item.strip() for item in accountsList.split(",") if item.strip()]
        self.readiness_timestamps["managed_accounts_at"] = self._stage_timestamp()
        self._managed_accounts_event.set()

    def currentTime(self, time_: int) -> None:  # noqa: N802
        dt = datetime.utcfromtimestamp(int(time_))
        self.last_server_time = dt
        self.readiness_timestamps["current_time_at"] = self._stage_timestamp()
        if self._current_time_req_id is not None and self._current_time_req_id in self._requests:
            request = self._requests[self._current_time_req_id]
            request.add({"server_time": dt, "local_received_at": datetime.utcnow()})
            request.complete()

    def error(self, *args: Any) -> None:  # noqa: A003
        req_id = int(args[0]) if args and isinstance(args[0], int) else -1
        code = int(args[1]) if len(args) > 1 and isinstance(args[1], int) else 0
        message = str(args[2]) if len(args) > 2 else str(args)
        self.last_error = {"request_id": req_id, "code": code, "message": message, "timestamp": datetime.utcnow().isoformat()}
        if req_id in self._submitted_orders:
            payload = {"order_id": req_id, "code": code, "message": message, "event": "error"}
            self._submitted_orders[req_id].setdefault("errors", []).append(payload)
            self._submitted_orders[req_id]["status_event"].set()
        if req_id == -1 and code in {326, 502, 504, 1100, 1300}:
            self._connect_error_event.set()
        if req_id in self._requests:
            self._requests[req_id].add_error(code, message)

    def connectionClosed(self) -> None:  # noqa: N802
        self.socket_connected = False
        self.last_disconnect_reason = self.last_disconnect_reason or "CONNECTION_CLOSED"
        self.readiness_timestamps["connection_closed_at"] = self._stage_timestamp()
        self._connection_closed_event.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        if reqId in self._requests:
            self._requests[reqId].add({"account": account, "tag": tag, "value": value, "currency": currency})

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        if reqId in self._requests:
            self._requests[reqId].complete()

    def position(self, account: str, contract: Contract, position: Decimal, avgCost: float) -> None:  # noqa: N802
        if self._positions_req_id in self._requests:
            self._requests[self._positions_req_id].add({"account": account, "contract": _contract_payload(contract), "position": str(position), "avg_cost": str(avgCost)})

    def positionEnd(self) -> None:  # noqa: N802
        if self._positions_req_id in self._requests:
            self._requests[self._positions_req_id].complete()

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState: OrderState) -> None:  # noqa: N802
        if orderId in self._submitted_orders:
            self._submitted_orders[orderId]["open_order"] = {"order_id": orderId, "contract": _contract_payload(contract), "order": _order_payload(order), "state": getattr(orderState, "status", None)}
            self._submitted_orders[orderId]["open_order_event"].set()
        if self._open_orders_req_id in self._requests:
            self._requests[self._open_orders_req_id].add({"order_id": orderId, "contract": _contract_payload(contract), "order": _order_payload(order), "state": getattr(orderState, "status", None)})

    def orderStatus(self, orderId: int, status: str, filled: Decimal, remaining: Decimal, avgFillPrice: float, *args: Any) -> None:  # noqa: N802
        if orderId in self._submitted_orders:
            payload = {"order_id": orderId, "status": status, "filled": str(filled), "remaining": str(remaining), "avg_fill_price": str(avgFillPrice), "event": "orderStatus"}
            self._submitted_orders[orderId].setdefault("statuses", []).append(payload)
            self._submitted_orders[orderId]["status_event"].set()
        if self._open_orders_req_id in self._requests:
            self._requests[self._open_orders_req_id].add({"order_id": orderId, "status": status, "filled": str(filled), "remaining": str(remaining), "avg_fill_price": str(avgFillPrice), "event": "orderStatus"})

    def openOrderEnd(self) -> None:  # noqa: N802
        if self._open_orders_req_id in self._requests:
            self._requests[self._open_orders_req_id].complete()

    def completedOrder(self, contract: Contract, order: Order, orderState: OrderState) -> None:  # noqa: N802
        for request in self._requests.values():
            if request.request_type == "completed_orders" and not request.completed:
                request.add({"contract": _contract_payload(contract), "order": _order_payload(order), "state": getattr(orderState, "status", None)})

    def completedOrdersEnd(self) -> None:  # noqa: N802
        for request in self._requests.values():
            if request.request_type == "completed_orders" and not request.completed:
                request.complete()

    def execDetails(self, reqId: int, contract: Contract, execution: Execution) -> None:  # noqa: N802
        order_id = int(getattr(execution, "orderId", 0) or 0)
        if order_id in self._submitted_orders:
            payload = {"contract": _contract_payload(contract), "execution": _execution_payload(execution)}
            self._submitted_orders[order_id].setdefault("executions", []).append(payload)
            self._submitted_orders[order_id]["execution_event"].set()
        if reqId in self._requests:
            self._requests[reqId].add({"contract": _contract_payload(contract), "execution": _execution_payload(execution)})

    def execDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        if reqId in self._requests:
            self._requests[reqId].complete()

    def commissionReport(self, commissionReport: Any) -> None:  # noqa: N802
        exec_id = getattr(commissionReport, "execId", None)
        payload = {"exec_id": exec_id, "commission": str(getattr(commissionReport, "commission", "")), "currency": getattr(commissionReport, "currency", None), "realized_pnl": str(getattr(commissionReport, "realizedPNL", ""))}
        for submission in self._submitted_orders.values():
            if any((row.get("execution") or {}).get("exec_id") == exec_id for row in submission.get("executions", [])):
                submission.setdefault("commission_reports", []).append(payload)
                submission["commission_event"].set()
        for request in self._requests.values():
            if request.request_type == "executions" and not request.completed:
                request.metadata.setdefault("commission_reports", []).append(payload)

    def contractDetails(self, reqId: int, contractDetails: ContractDetails) -> None:  # noqa: N802
        if reqId in self._requests:
            self._requests[reqId].add(_contract_details_payload(contractDetails))

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        if reqId in self._requests:
            self._requests[reqId].complete()

    def current_time(self) -> dict[str, Any]:
        request = self._new_request("current_time")
        self._current_time_req_id = request.request_id
        self.reqCurrentTime()
        return self._wait(request).results[-1]

    def account_rows(self) -> list[BrokerAccount]:
        return [
            BrokerAccount(
                account_id=account_id,
                alias=_mask_account(account_id),
                environment=BrokerEnvironment.PAPER if account_id.upper().startswith("DU") else BrokerEnvironment.UNVERIFIED,
                paper_verified=account_id == self.config.expected_account and account_id in self.config.account_allow_list and account_id.upper().startswith("DU"),
                allowed=account_id in self.config.account_allow_list,
                base_currency="USD",
                account_type="PAPER" if account_id.upper().startswith("DU") else "UNVERIFIED",
            )
            for account_id in self.managed_accounts
        ]

    def account_summary(self) -> list[dict[str, Any]]:
        request = self._new_request("account_summary")
        tags = "AccountType,NetLiquidation,TotalCashValue,AvailableFunds,BuyingPower,ExcessLiquidity,InitMarginReq,MaintMarginReq,RealizedPnL,UnrealizedPnL,BaseCurrency"
        self.reqAccountSummary(request.request_id, "All", tags)
        try:
            return self._wait(request).results
        finally:
            self.cancelAccountSummary(request.request_id)

    def positions_rows(self) -> list[dict[str, Any]]:
        request = self._new_request("positions")
        self._positions_req_id = request.request_id
        self.reqPositions()
        try:
            return self._wait(request).results
        finally:
            try:
                self.cancelPositions()
            except Exception:
                pass

    def open_order_rows(self) -> list[dict[str, Any]]:
        request = self._new_request("open_orders")
        self._open_orders_req_id = request.request_id
        self.reqOpenOrders()
        return self._wait(request).results

    def completed_order_rows(self) -> tuple[str, list[dict[str, Any]]]:
        if not hasattr(self, "reqCompletedOrders"):
            return "UNSUPPORTED", []
        request = self._new_request("completed_orders")
        self.reqCompletedOrders(False)
        return "REAL_IBAPI", self._wait(request).results

    def execution_rows(self) -> list[dict[str, Any]]:
        request = self._new_request("executions")
        self.reqExecutions(request.request_id, ExecutionFilter())
        waited = self._wait(request)
        commissions = {row.get("exec_id"): row for row in waited.metadata.get("commission_reports", [])}
        for row in waited.results:
            exec_id = row.get("execution", {}).get("exec_id")
            if exec_id in commissions:
                row["commission_report"] = commissions[exec_id]
        return waited.results

    def contract_details_rows(self, symbol: str, currency: str) -> list[dict[str, Any]]:
        request = self._new_request("contract_details")
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "CASH"
        contract.currency = currency
        contract.exchange = "IDEALPRO"
        self.reqContractDetails(request.request_id, contract)
        return self._wait(request).results

    def place_fx5e_market_order(self, *, account_id: str, side: str, quantity: Decimal, order_ref: str) -> dict[str, Any]:
        if "FX5E_ACCEPTANCE" not in order_ref:
            raise BrokerCapabilityError("FX5E_ORDER_REF_REQUIRED", "FX-5E submissions require an FX5E_ACCEPTANCE order reference", status_code=403)
        return self._place_fx_market_order(account_id=account_id, side=side, quantity=quantity, order_ref=order_ref, instrument_id="FX:EURUSD")

    def place_ai_auto_paper_market_order(self, *, account_id: str, instrument_id: str, side: str, quantity: Decimal, order_ref: str) -> dict[str, Any]:
        if not order_ref.startswith("AI_AUTO_PAPER_"):
            raise BrokerCapabilityError("AI_AUTO_PAPER_ORDER_REF_REQUIRED", "AI autonomous submissions require an AI_AUTO_PAPER order reference", status_code=403)
        if instrument_id.upper() not in {"FX:EURUSD", "FX:GBPUSD", "FX:USDJPY", "EURUSD", "GBPUSD", "USDJPY"}:
            raise BrokerCapabilityError("AI_AUTO_PAPER_SYMBOL_BLOCKED", "AI autonomous paper trading is limited to EURUSD, GBPUSD, and USDJPY", status_code=403)
        return self._place_fx_market_order(account_id=account_id, side=side, quantity=quantity, order_ref=order_ref, instrument_id=instrument_id)

    def place_fx_flatten_market_order(self, *, account_id: str, instrument_id: str, side: str, quantity: Decimal, order_ref: str) -> dict[str, Any]:
        if not order_ref.startswith("IBKR_PAPER_FLATTEN_"):
            raise BrokerCapabilityError("IBKR_PAPER_FLATTEN_REF_REQUIRED", "paper flatten submissions require an IBKR_PAPER_FLATTEN order reference", status_code=403)
        if not instrument_id.upper().startswith("FX:"):
            raise BrokerCapabilityError("IBKR_PAPER_FLATTEN_FX_ONLY", "paper flatten only supports FX positions", status_code=403)
        return self._place_fx_market_order(account_id=account_id, side=side, quantity=quantity, order_ref=order_ref, instrument_id=instrument_id)

    def place_ai_auto_paper_protective_orders(
        self,
        *,
        account_id: str,
        instrument_id: str,
        entry_side: str,
        quantity: Decimal,
        stop_price: Decimal,
        take_profit: Decimal,
        order_ref: str,
    ) -> dict[str, Any]:
        if not order_ref.startswith("AI_AUTO_PAPER_"):
            raise BrokerCapabilityError("AI_AUTO_PAPER_ORDER_REF_REQUIRED", "AI autonomous protective orders require an AI_AUTO_PAPER order reference", status_code=403)
        if instrument_id.upper() not in {"FX:EURUSD", "FX:GBPUSD", "FX:USDJPY", "EURUSD", "GBPUSD", "USDJPY"}:
            raise BrokerCapabilityError("AI_AUTO_PAPER_SYMBOL_BLOCKED", "AI autonomous paper trading is limited to EURUSD, GBPUSD, and USDJPY", status_code=403)
        if not self.api_ready or self.next_valid_order_id is None:
            raise BrokerCapabilityError("REAL_IBKR_API_NOT_READY", "real TWS API is not ready", status_code=503)
        unsupported = _encoded_unsupported_order_attributes()
        if unsupported:
            raise BrokerCapabilityError("IBKR_UNSUPPORTED_ORDER_ATTRIBUTE", f"outgoing order encoder contains unsupported attributes: {unsupported}", status_code=422)
        child_side = "SELL" if entry_side.upper() == "BUY" else "BUY"
        oca_group = f"{order_ref}_OCA"
        contract, _ = self._fx_market_contract_order(account_id=account_id, instrument_id=instrument_id, side=child_side, quantity=quantity, order_ref=order_ref)
        children: list[tuple[str, Order]] = []
        stop = Order()
        stop.action = child_side
        stop.orderType = "STP"
        stop.totalQuantity = float(quantity)
        stop.auxPrice = float(stop_price)
        stop.tif = "DAY"
        stop.account = account_id
        stop.orderRef = f"{order_ref}_SL"
        stop.ocaGroup = oca_group
        stop.ocaType = 1
        stop.transmit = True
        children.append(("stop_loss", stop))
        target = Order()
        target.action = child_side
        target.orderType = "LMT"
        target.totalQuantity = float(quantity)
        target.lmtPrice = float(take_profit)
        target.tif = "DAY"
        target.account = account_id
        target.orderRef = f"{order_ref}_TP"
        target.ocaGroup = oca_group
        target.ocaType = 1
        target.transmit = True
        children.append(("take_profit", target))
        orders: list[dict[str, Any]] = []
        for role, order in children:
            order_id = int(self.next_valid_order_id)
            self.next_valid_order_id += 1
            state = {
                "order_id": order_id,
                "order_ref": getattr(order, "orderRef", None),
                "role": role,
                "open_order_event": threading.Event(),
                "status_event": threading.Event(),
                "execution_event": threading.Event(),
                "commission_event": threading.Event(),
                "place_order_called": False,
                "submitted_at": self._stage_timestamp(),
            }
            self._submitted_orders[order_id] = state
            self.placeOrder(order_id, contract, order)
            state["place_order_called"] = True
            acked = state["open_order_event"].wait(self.config.request_timeout_seconds) or state["status_event"].wait(0.1)
            state["submission_state"] = "ACKNOWLEDGED" if acked else "SUBMISSION_UNKNOWN"
            orders.append(dict(state))
        return {
            "status": "SUBMITTED",
            "order_ref": order_ref,
            "oca_group": oca_group,
            "place_order_calls": len(orders),
            "orders": orders,
        }

    def _place_fx_market_order(self, *, account_id: str, side: str, quantity: Decimal, order_ref: str, instrument_id: str) -> dict[str, Any]:
        if not self.api_ready or self.next_valid_order_id is None:
            raise BrokerCapabilityError("REAL_IBKR_API_NOT_READY", "real TWS API is not ready", status_code=503)
        unsupported = _encoded_unsupported_order_attributes()
        if unsupported:
            raise BrokerCapabilityError("IBKR_UNSUPPORTED_ORDER_ATTRIBUTE", f"outgoing order encoder contains unsupported attributes: {unsupported}", status_code=422)
        max_recorded_order_id = _max_recorded_ibkr_order_id()
        if max_recorded_order_id is not None and int(self.next_valid_order_id) <= max_recorded_order_id:
            self.next_valid_order_id = max_recorded_order_id + 1
        order_id = int(self.next_valid_order_id)
        self.next_valid_order_id += 1
        contract, order = self._fx_market_contract_order(account_id=account_id, instrument_id=instrument_id, side=side, quantity=quantity, order_ref=order_ref)
        state = {
            "order_id": order_id,
            "order_ref": order_ref,
            "open_order_event": threading.Event(),
            "status_event": threading.Event(),
            "execution_event": threading.Event(),
            "commission_event": threading.Event(),
            "place_order_called": False,
            "submitted_at": self._stage_timestamp(),
        }
        self._submitted_orders[order_id] = state
        self.placeOrder(order_id, contract, order)
        state["place_order_called"] = True
        acked = state["open_order_event"].wait(self.config.request_timeout_seconds) or state["status_event"].wait(0.1)
        if not acked:
            state["submission_state"] = "SUBMISSION_UNKNOWN"
            return dict(state)
        state["submission_state"] = "ACKNOWLEDGED"
        state["execution_event"].wait(5)
        state["commission_event"].wait(3)
        return dict(state)

    def dry_run_fx5e_market_order(self, *, account_id: str, side: str, quantity: Decimal, order_ref: str) -> dict[str, Any]:
        contract, order = self._fx_market_contract_order(account_id=account_id, instrument_id="FX:EURUSD", side=side, quantity=quantity, order_ref=order_ref)
        unsupported = _encoded_unsupported_order_attributes()
        return {
            "status": "COMPATIBLE" if not unsupported else "BLOCKED",
            "unsupported_attributes": unsupported,
            "contract": _contract_payload(contract),
            "order": self._safe_order_diagnostic(order),
            "encoded_fields_absent": {attr: attr not in unsupported for attr in sorted(IBKR_UNSUPPORTED_ORDER_ATTRIBUTES)},
        }

    def _fx5e_contract_order(self, *, account_id: str, side: str, quantity: Decimal, order_ref: str) -> tuple[Any, Any]:
        return self._fx_market_contract_order(account_id=account_id, instrument_id="FX:EURUSD", side=side, quantity=quantity, order_ref=order_ref)

    def _fx_market_contract_order(self, *, account_id: str, instrument_id: str, side: str, quantity: Decimal, order_ref: str) -> tuple[Any, Any]:
        pair = instrument_id.upper().replace("FX:", "")
        if len(pair) != 6 or not pair.isalpha():
            raise BrokerCapabilityError("FX_PAIR_UNSUPPORTED", "unsupported FX pair", status_code=403)
        base, quote = pair[:3], pair[3:]
        con_ids = {"EURUSD": 12087792}
        contract = Contract()
        if pair in con_ids:
            contract.conId = con_ids[pair]
        contract.symbol = base
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = quote
        order = Order()
        order.action = side.upper()
        order.orderType = "MKT"
        order.totalQuantity = float(quantity)
        order.tif = "DAY"
        order.account = account_id
        order.orderRef = order_ref
        order.transmit = True
        return contract, order

    def _safe_order_diagnostic(self, order: Any) -> dict[str, Any]:
        return {
            "action": getattr(order, "action", None),
            "orderType": getattr(order, "orderType", None),
            "totalQuantity": str(getattr(order, "totalQuantity", "")),
            "tif": getattr(order, "tif", None),
            "account_masked": _masked(getattr(order, "account", None)),
            "orderRef": getattr(order, "orderRef", None),
            "transmit": getattr(order, "transmit", None),
            "parentId": getattr(order, "parentId", None),
            "ocaGroup": getattr(order, "ocaGroup", None),
            "auxPrice": str(getattr(order, "auxPrice", "")),
            "lmtPrice": str(getattr(order, "lmtPrice", "")),
            "outsideRth": getattr(order, "outsideRth", None),
            "cashQty": str(getattr(order, "cashQty", "")),
        }


def _contract_payload(contract: Any) -> dict[str, Any]:
    return {
        "con_id": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", None),
        "security_type": getattr(contract, "secType", None),
        "exchange": getattr(contract, "exchange", None),
        "primary_exchange": getattr(contract, "primaryExchange", None),
        "currency": getattr(contract, "currency", None),
        "local_symbol": getattr(contract, "localSymbol", None),
        "trading_class": getattr(contract, "tradingClass", None),
    }


def _contract_details_payload(details: Any) -> dict[str, Any]:
    contract = getattr(details, "contract", None)
    return {
        "contract": _contract_payload(contract),
        "minimum_tick": str(getattr(details, "minTick", "")),
        "market_name": getattr(details, "marketName", None),
        "valid_exchanges": getattr(details, "validExchanges", None),
        "market_rule_ids": [item for item in str(getattr(details, "marketRuleIds", "") or "").split(",") if item],
        "long_name": getattr(details, "longName", None),
        "price_magnifier": getattr(details, "priceMagnifier", None),
        "time_zone_id": getattr(details, "timeZoneId", None),
    }


def _order_payload(order: Any) -> dict[str, Any]:
    return {
        "account": getattr(order, "account", None),
        "perm_id": getattr(order, "permId", None),
        "client_id": getattr(order, "clientId", None),
        "order_ref": getattr(order, "orderRef", None),
        "action": getattr(order, "action", None),
        "order_type": getattr(order, "orderType", None),
        "total_quantity": str(getattr(order, "totalQuantity", "")),
        "limit_price": str(getattr(order, "lmtPrice", "")),
        "stop_price": str(getattr(order, "auxPrice", "")),
        "time_in_force": getattr(order, "tif", None),
    }


def _execution_payload(execution: Any) -> dict[str, Any]:
    return {
        "exec_id": getattr(execution, "execId", None),
        "account": getattr(execution, "acctNumber", None),
        "order_id": getattr(execution, "orderId", None),
        "perm_id": getattr(execution, "permId", None),
        "client_id": getattr(execution, "clientId", None),
        "side": getattr(execution, "side", None),
        "quantity": str(getattr(execution, "shares", "")),
        "price": str(getattr(execution, "price", "")),
        "time": getattr(execution, "time", None),
        "exchange": getattr(execution, "exchange", None),
        "order_ref": getattr(execution, "orderRef", None),
    }


class IbkrConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    API_READY = "API_READY"
    DISCONNECTING = "DISCONNECTING"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    SHUTDOWN = "SHUTDOWN"


class IbkrSessionManager:
    """Process-scoped owner for the single real TWS API session."""

    def __init__(self, config: IBKRConfiguration) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._client: RealTwsReadOnlyClient | None = None
        self.state = IbkrConnectionState.DISCONNECTED
        self.connection_attempt_id: str | None = None
        self.reconnect_task_active = False
        self.shutdown_requested = False
        self.last_transition_at = datetime.now(timezone.utc).isoformat()

    @property
    def client(self) -> RealTwsReadOnlyClient | None:
        return self._client

    def connect_once(self) -> RealTwsReadOnlyClient:
        with self._lock:
            if self.shutdown_requested:
                raise BrokerCapabilityError("IBKR_MANAGER_SHUTTING_DOWN", "IBKR session manager is shutting down", status_code=503)
            if self._client and self._client.api_ready and self._client.isConnected():
                self.state = IbkrConnectionState.API_READY
                return self._client
            if self._client and (self._client.isConnected() or self._client.readiness_diagnostics().get("event_loop_thread_alive")):
                self._client.disconnect_clean()
            self.state = IbkrConnectionState.CONNECTING
            self._transition()
            self._client = RealTwsReadOnlyClient(self.config)
            try:
                self._client.start_and_wait_ready()
                self.connection_attempt_id = self._client.connection_attempt_id
                self.state = IbkrConnectionState.API_READY
                self._transition()
                return self._client
            except Exception:
                self.state = IbkrConnectionState.FAILED
                self._transition()
                if self._client:
                    self._client.disconnect_clean()
                    self._client = None
                raise

    def disconnect_once(self, reason: str = "MANAGER_DISCONNECT") -> None:
        with self._lock:
            if self.state == IbkrConnectionState.SHUTDOWN and not self._client:
                return
            self.state = IbkrConnectionState.DISCONNECTING
            self._transition()
            client = self._client
            self._client = None
            if client:
                client.last_disconnect_reason = reason
                client.disconnect_clean()
            self.state = IbkrConnectionState.DISCONNECTED
            self._transition()

    def reconnect_serialized(self) -> RealTwsReadOnlyClient:
        with self._lock:
            if self.reconnect_task_active:
                if self._client and self._client.api_ready:
                    return self._client
                raise BrokerCapabilityError("IBKR_RECONNECT_IN_PROGRESS", "IBKR reconnect already in progress", status_code=409)
            self.reconnect_task_active = True
        try:
            with self._lock:
                self.state = IbkrConnectionState.RECONNECTING
                self._transition()
            self.disconnect_once("MANAGER_RECONNECT")
            time.sleep(1)
            return self.connect_once()
        finally:
            with self._lock:
                self.reconnect_task_active = False

    def shutdown(self) -> None:
        with self._lock:
            self.shutdown_requested = True
        self.disconnect_once("APP_SHUTDOWN")
        with self._lock:
            self.state = IbkrConnectionState.SHUTDOWN
            self._transition()

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            client_diag = self._client.readiness_diagnostics() if self._client else {}
            data = {
                "manager_state": self.state.value,
                "process_id": os.getpid(),
                "thread_id": threading.get_ident(),
                "client_id": self.config.client_id,
                "connection_attempt_id": self.connection_attempt_id or client_diag.get("connection_attempt_id"),
                "event_loop_thread_alive": bool(client_diag.get("event_loop_thread_alive", False)),
                "socket_connected": bool(client_diag.get("socket_connected", False)),
                "reconnect_task_active": self.reconnect_task_active,
                "shutdown_requested": self.shutdown_requested,
                "last_transition_at": self.last_transition_at,
            }
            data.update(client_diag)
            return data

    def _transition(self) -> None:
        self.last_transition_at = datetime.now(timezone.utc).isoformat()


class IBKRPaperAdapter:
    name = "ibkr"

    def __init__(self, config: IBKRConfiguration | None = None) -> None:
        self.config = config or ibkr_config
        self.session = new_session()
        self.session_manager = IbkrSessionManager(self.config)
        self.real_client: RealTwsReadOnlyClient | None = None
        self._last_account_summary: list[dict[str, Any]] = []
        self._last_positions: list[dict[str, Any]] = []
        self._last_open_orders: list[dict[str, Any]] = []
        self._last_completed_orders: tuple[str, list[dict[str, Any]]] = ("NOT_RUN", [])
        self._last_executions: list[dict[str, Any]] = []
        self._last_contract_details: dict[str, dict[str, Any]] = {}
        self.capabilities = BrokerCapabilities(
            broker="ibkr",
            environment="PAPER",
            account_types={"PAPER"},
            asset_types={"EQUITY", "ETF", "FOREX", "INDEX"},
            order_types={"MARKET", "LIMIT", "STOP", "STOP_LIMIT"},
            time_in_force={"DAY", "GTC"},
            market_data={"QUOTE", "BID", "ASK", "LAST", "MIDPOINT"},
            historical_data={"1 min", "5 mins", "15 mins", "1 hour", "1 day"},
            supports_cancel=True,
            supports_modify=False,
            supports_streaming=False,
            supports_currency_conversion=False,
            live_trading_enabled=False,
        )

    async def connect(self) -> BrokerHealth:
        if self.config.mode == "DISABLED" or not self.config.enabled:
            from backend.brokers.errors import BrokerSafetyError

            self.session.fail("IBKR_DISABLED")
            raise BrokerSafetyError("IBKR_DISABLED", "IBKR is disabled by configuration", status_code=403)
        if self.config.mode == "PAPER":
            if self.config.simulated:
                from backend.brokers.errors import BrokerSafetyError

                self.session.fail("REAL_IBKR_PAPER_REQUIRES_NON_SIMULATED_ADAPTER")
                raise BrokerSafetyError("REAL_IBKR_PAPER_REQUIRES_NON_SIMULATED_ADAPTER", "IBKR_MODE=PAPER cannot use the fixture/simulated adapter", status_code=403)
            if importlib.util.find_spec("ibapi") is None:
                from backend.brokers.errors import BrokerSafetyError

                self.session.fail("REAL_IBKR_LIBRARY_UNAVAILABLE")
                raise BrokerSafetyError("REAL_IBKR_LIBRARY_UNAVAILABLE", "ibapi is not installed; real TWS/Gateway connectivity is unavailable", status_code=503)
        if self.config.real_paper_mode:
            self.real_client = await asyncio.to_thread(self.session_manager.connect_once)
            verified = self._real_account_verified()
            self.session.connect(
                server_version=str(getattr(self.real_client, "serverVersion", lambda: "unknown")()),
                verified=verified,
                order_enabled=False,
            )
        else:
            verified = verify_paper(self.config)
            self.session.connect(server_version="simulated-ibkr-phase11" if self.config.simulated else "unverified", verified=verified, order_enabled=self.config.order_submission_enabled and not self.config.readonly)
        return await self.health()

    async def disconnect(self) -> BrokerHealth:
        if self.config.real_paper_mode:
            await asyncio.to_thread(self.session_manager.disconnect_once)
            self.real_client = None
        elif self.real_client:
            self.real_client.disconnect_clean()
            self.real_client = None
        self.session.disconnect()
        return await self.health()

    async def reconnect(self) -> BrokerHealth:
        self.session.reconnecting()
        if self.config.real_paper_mode:
            self.real_client = await asyncio.to_thread(self.session_manager.reconnect_serialized)
            verified = self._real_account_verified()
            self.session.connect(
                server_version=str(getattr(self.real_client, "serverVersion", lambda: "unknown")()),
                verified=verified,
                order_enabled=False,
            )
            return await self.health()
        await self.disconnect()
        await asyncio.sleep(2)
        return await self.connect()

    async def shutdown(self) -> None:
        if self.config.real_paper_mode:
            await asyncio.to_thread(self.session_manager.shutdown)
            self.real_client = None

    async def health(self) -> BrokerHealth:
        return self.session.health(host=self.config.host, port=self.config.port, client_id=self.config.client_id)

    async def accounts(self) -> list[BrokerAccount]:
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            return self.real_client.account_rows()  # type: ignore[union-attr]
        return discover_accounts(self.config)

    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot:
        self._assert_account_allowed(account_id)
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            rows = self.real_client.account_summary()  # type: ignore[union-attr]
            self._last_account_summary = rows
            by_account: dict[str, dict[str, list[dict[str, str]]]] = {}
            for row in rows:
                by_account.setdefault(row["account"], {}).setdefault(row["tag"], []).append({"value": row["value"], "currency": row.get("currency") or ""})
            data = by_account.get(account_id, {})
            base_currency = _summary_value(data, "BaseCurrency", default="USD") or "USD"
            snap = BrokerAccountSnapshot(
                account_id=account_id,
                environment=BrokerEnvironment.PAPER,
                net_liquidation=_dec(_summary_value(data, "NetLiquidation", currency=base_currency)),
                buying_power=_dec(_summary_value(data, "BuyingPower", currency=base_currency)),
                available_funds=_dec(_summary_value(data, "AvailableFunds", currency=base_currency)),
                excess_liquidity=_dec(_summary_value(data, "ExcessLiquidity", currency=base_currency)),
                initial_margin=_dec(_summary_value(data, "InitMarginReq", currency=base_currency)),
                maintenance_margin=_dec(_summary_value(data, "MaintMarginReq", currency=base_currency)),
                realized_pnl=_dec(_summary_value(data, "RealizedPnL", currency=base_currency)),
                unrealized_pnl=_dec(_summary_value(data, "UnrealizedPnL", currency=base_currency)),
                cash=_summary_cash_balances(data, base_currency=base_currency),
            )
            return snap
        return snapshot(account_id, self.config)

    async def positions(self, account_id: str) -> list[BrokerPosition]:
        self._assert_account_allowed(account_id)
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            rows = self.real_client.positions_rows()  # type: ignore[union-attr]
            self._last_positions = rows
            out: list[BrokerPosition] = []
            for row in rows:
                if row.get("account") != account_id:
                    continue
                contract = row.get("contract", {})
                symbol = str(contract.get("symbol") or "")
                currency = str(contract.get("currency") or "")
                instrument = f"FX:{symbol}{currency}" if contract.get("security_type") == "CASH" else f"IBKR:{contract.get('con_id')}"
                out.append(BrokerPosition(instrument_id=instrument, con_id=contract.get("con_id"), quantity=_dec(row.get("position")), average_cost=_dec(row.get("avg_cost")), currency=currency or "USD"))
            return out
        return broker_positions(account_id)

    async def open_orders(self, account_id: str) -> list[BrokerOrder]:
        self._assert_account_allowed(account_id)
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            rows = self.real_client.open_order_rows()  # type: ignore[union-attr]
            self._last_open_orders = rows
            return [_broker_order_from_real(row, account_id) for row in rows if (row.get("order") or {}).get("account") in {None, "", account_id}]
        return order_book.open_orders(account_id)

    async def completed_orders(self, account_id: str) -> tuple[str, list[BrokerOrder]]:
        self._assert_account_allowed(account_id)
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            status, rows = self.real_client.completed_order_rows()  # type: ignore[union-attr]
            self._last_completed_orders = (status, rows)
            return status, [_broker_order_from_real(row, account_id) for row in rows if (row.get("order") or {}).get("account") in {None, "", account_id}]
        return "FIXTURE", []

    async def executions(self, account_id: str, since: datetime | None = None) -> list[BrokerExecution]:
        self._assert_account_allowed(account_id)
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            rows = self.real_client.execution_rows()  # type: ignore[union-attr]
            self._last_executions = rows
            out = [_broker_execution_from_real(row) for row in rows if (row.get("execution") or {}).get("account") == account_id]
            return [row for row in out if since is None or row.timestamp >= since]
        rows = order_book.executions_for(account_id)
        return [row for row in rows if since is None or row.timestamp >= since]

    async def resolve_contract(self, instrument_id: str) -> BrokerContract:
        if self.config.real_paper_mode:
            self._ensure_real_ready()
            return self._resolve_real_contract(instrument_id)
        contract = resolve_contract(instrument_id)
        self.capabilities.require_asset(contract.asset_type)
        return contract

    async def quote(self, instrument_id: str) -> BrokerQuote:
        return quote_for(instrument_id, mode=self.config.market_data_mode)

    async def historical_bars(self, instrument_id: str, *, bar_size: str, duration: str) -> list[BrokerBar]:
        return historical_bars(instrument_id, bar_size=bar_size, duration=duration)

    async def reconcile(self, account_id: str) -> BrokerReconciliationResult:
        self._assert_account_allowed(account_id)
        return reconcile_broker(account_id)

    async def submit_order(self, command: BrokerOrderCommand) -> BrokerOrderReceipt:
        if self.config.real_paper_mode:
            from backend.brokers.errors import BrokerSafetyError

            raise BrokerSafetyError("BROKER_ORDER_SUBMISSION_DISABLED", "FX-5D read-only IBKR mode forbids placeOrder", status_code=403)
        contract = await self.resolve_contract(command.instrument_id)
        self.capabilities.require_asset(contract.asset_type)
        self.capabilities.require_order(command.order_type, command.time_in_force)
        self._assert_account_allowed(command.account_id)
        paper_verified = any(row.account_id == command.account_id and row.paper_verified and row.allowed for row in await self.accounts())
        return order_book.submit(command, paper_verified=paper_verified, order_enabled=self.session.order_submission_status == "ENABLED")

    async def submit_fx5e_acceptance_order(self, command: BrokerOrderCommand) -> dict[str, Any]:
        from backend.brokers.errors import BrokerSafetyError

        if not self.config.real_paper_mode:
            raise BrokerSafetyError("REAL_IBKR_PAPER_REQUIRED", "FX-5E requires real IBKR paper mode", status_code=403)
        if self.config.live_trading_enabled:
            raise BrokerSafetyError("LIVE_TRADING_BLOCKED", "live trading is disabled for FX-5E", status_code=403)
        if not command.user_approval:
            raise BrokerSafetyError("USER_APPROVAL_REQUIRED", "manual approval is required", status_code=403)
        if "FX5E_ACCEPTANCE" not in str(command.correlation_id or ""):
            raise BrokerSafetyError("FX5E_ORDER_REF_REQUIRED", "FX-5E order reference is required", status_code=403)
        if command.instrument_id.upper() not in {"FX:EURUSD", "EURUSD"}:
            raise BrokerSafetyError("FX5E_EURUSD_ONLY", "FX-5E only permits EUR/USD", status_code=403)
        if command.order_type.upper() != "MARKET":
            raise BrokerSafetyError("FX5E_MARKET_ONLY", "FX-5E only permits a market entry", status_code=403)
        self._assert_account_allowed(command.account_id)
        self._ensure_real_ready()
        return self.real_client.place_fx5e_market_order(  # type: ignore[union-attr]
            account_id=command.account_id,
            side=command.side,
            quantity=command.quantity,
            order_ref=command.correlation_id or "",
        )

    async def submit_ai_auto_paper_order(self, command: BrokerOrderCommand) -> dict[str, Any]:
        if not self.config.real_paper_mode:
            raise BrokerSafetyError("REAL_IBKR_PAPER_REQUIRED", "AI autonomous trading requires real IBKR paper mode", status_code=403)
        if self.config.live_trading_enabled:
            raise BrokerSafetyError("LIVE_TRADING_BLOCKED", "live trading is disabled for AI autonomous trading", status_code=403)
        if command.user_approval:
            raise BrokerSafetyError("AI_AUTO_PAPER_NO_MANUAL_APPROVAL", "AI autonomous orders must not use manual approval", status_code=403)
        if not str(command.correlation_id or "").startswith("AI_AUTO_PAPER_"):
            raise BrokerSafetyError("AI_AUTO_PAPER_ORDER_REF_REQUIRED", "AI autonomous order reference is required", status_code=403)
        if command.order_type.upper() != "MARKET":
            raise BrokerSafetyError("AI_AUTO_PAPER_MARKET_ONLY", "AI autonomous trading only permits market entries", status_code=403)
        if command.quantity <= 0 or command.quantity != command.approved_quantity:
            raise BrokerSafetyError("BROKER_ORDER_QUANTITY_MISMATCH", "order quantity does not match approved risk quantity", status_code=422)
        max_quantity = _ai_auto_paper_max_quantity()
        if command.quantity > max_quantity:
            raise BrokerSafetyError("AI_AUTO_PAPER_SIZE_LIMIT", f"AI autonomous quantity may not exceed {max_quantity} FX units", status_code=403)
        self._assert_account_allowed(command.account_id)
        paper_verified = any(row.account_id == command.account_id and row.paper_verified and row.allowed for row in await self.accounts())
        if not paper_verified:
            raise BrokerSafetyError("BROKER_ENVIRONMENT_UNVERIFIED", "paper account is not verified", status_code=403)
        self._ensure_real_ready()
        await self._require_fx_cash_funding(command)
        return self.real_client.place_ai_auto_paper_market_order(  # type: ignore[union-attr]
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            side=command.side,
            quantity=command.quantity,
            order_ref=command.correlation_id or "",
        )

    async def submit_paper_fx_flatten_order(self, command: BrokerOrderCommand) -> dict[str, Any]:
        if not self.config.real_paper_mode:
            raise BrokerSafetyError("REAL_IBKR_PAPER_REQUIRED", "FX flatten requires real IBKR paper mode", status_code=403)
        if self.config.live_trading_enabled:
            raise BrokerSafetyError("LIVE_TRADING_BLOCKED", "live trading is disabled for FX flatten", status_code=403)
        if not command.user_approval:
            raise BrokerSafetyError("USER_APPROVAL_REQUIRED", "FX flatten requires manual user approval", status_code=403)
        if not str(command.correlation_id or "").startswith("IBKR_PAPER_FLATTEN_"):
            raise BrokerSafetyError("IBKR_PAPER_FLATTEN_REF_REQUIRED", "FX flatten order reference is required", status_code=403)
        if command.order_type.upper() != "MARKET":
            raise BrokerSafetyError("IBKR_PAPER_FLATTEN_MARKET_ONLY", "FX flatten only permits market orders", status_code=403)
        if command.quantity <= 0 or command.quantity != command.approved_quantity:
            raise BrokerSafetyError("BROKER_ORDER_QUANTITY_MISMATCH", "order quantity does not match approved flatten quantity", status_code=422)
        max_quantity = _ai_auto_paper_max_quantity()
        if command.quantity > max_quantity:
            raise BrokerSafetyError("IBKR_PAPER_FLATTEN_SIZE_LIMIT", f"FX flatten quantity may not exceed {max_quantity} FX units per order", status_code=403)
        self._assert_account_allowed(command.account_id)
        paper_verified = any(row.account_id == command.account_id and row.paper_verified and row.allowed for row in await self.accounts())
        if not paper_verified:
            raise BrokerSafetyError("BROKER_ENVIRONMENT_UNVERIFIED", "paper account is not verified", status_code=403)
        self._ensure_real_ready()
        await self._require_fx_cash_funding(command)
        return self.real_client.place_fx_flatten_market_order(  # type: ignore[union-attr]
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            side=command.side,
            quantity=command.quantity,
            order_ref=command.correlation_id or "",
        )

    async def submit_ai_auto_paper_protective_orders(
        self,
        command: BrokerOrderCommand,
        *,
        parent_receipt: dict[str, Any],
        stop_price: Decimal,
        take_profit: Decimal,
        quantity: Decimal,
    ) -> dict[str, Any]:
        from backend.brokers.errors import BrokerSafetyError

        if not self.config.real_paper_mode:
            raise BrokerSafetyError("REAL_IBKR_PAPER_REQUIRED", "AI autonomous trading requires real IBKR paper mode", status_code=403)
        if self.config.live_trading_enabled:
            raise BrokerSafetyError("LIVE_TRADING_BLOCKED", "live trading is disabled for AI autonomous trading", status_code=403)
        if command.user_approval:
            raise BrokerSafetyError("AI_AUTO_PAPER_NO_MANUAL_APPROVAL", "AI autonomous protective orders must not use manual approval", status_code=403)
        if not str(command.correlation_id or "").startswith("AI_AUTO_PAPER_"):
            raise BrokerSafetyError("AI_AUTO_PAPER_ORDER_REF_REQUIRED", "AI autonomous order reference is required", status_code=403)
        max_quantity = _ai_auto_paper_max_quantity()
        if quantity <= 0 or quantity > max_quantity:
            raise BrokerSafetyError("AI_AUTO_PAPER_SIZE_LIMIT", f"AI autonomous protective quantity may not exceed {max_quantity} FX units", status_code=403)
        if str(parent_receipt.get("submission_state") or "") == "SUBMISSION_UNKNOWN":
            raise BrokerSafetyError("AI_AUTO_PAPER_ENTRY_UNCONFIRMED", "cannot place protective orders until entry submission is confirmed", status_code=409)
        self._assert_account_allowed(command.account_id)
        paper_verified = any(row.account_id == command.account_id and row.paper_verified and row.allowed for row in await self.accounts())
        if not paper_verified:
            raise BrokerSafetyError("BROKER_ENVIRONMENT_UNVERIFIED", "paper account is not verified", status_code=403)
        self._ensure_real_ready()
        exit_side = "SELL" if command.side.upper() == "BUY" else "BUY"
        await self._require_fx_cash_funding(command, side=exit_side, quantity=quantity, price=stop_price, role="stop_loss")
        await self._require_fx_cash_funding(command, side=exit_side, quantity=quantity, price=take_profit, role="take_profit")
        return self.real_client.place_ai_auto_paper_protective_orders(  # type: ignore[union-attr]
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            entry_side=command.side,
            quantity=quantity,
            stop_price=stop_price,
            take_profit=take_profit,
            order_ref=command.correlation_id or "",
        )

    async def validate_ai_auto_paper_funding_plan(
        self,
        command: BrokerOrderCommand,
        *,
        entry_price: Decimal,
        stop_price: Decimal,
        take_profit: Decimal,
    ) -> dict[str, Any]:
        entry = await self._fx_cash_funding(command, price=entry_price, role="entry")
        side = command.side.upper()
        exit_side = "SELL" if side == "BUY" else "BUY"
        stop = await self._fx_cash_funding(command, side=exit_side, quantity=command.quantity, price=stop_price, role="stop_loss")
        target = await self._fx_cash_funding(command, side=exit_side, quantity=command.quantity, price=take_profit, role="take_profit")
        checks = [entry, stop, target]
        rejected = [row for row in checks if row["funding_status"] == "INSUFFICIENT"]
        if rejected:
            details = {"checks": checks, "failed_check": rejected[0], "account_capability": entry["account_capability"]}
            exc = BrokerSafetyError("INSUFFICIENT_SETTLEMENT_CURRENCY", "FX order funding plan would exceed real IBKR settlement cash", status_code=422)
            exc.details = details  # type: ignore[attr-defined]
            raise exc
        return {"status": "FUNDED", "checks": checks, "account_capability": entry["account_capability"]}

    async def fx_cash_funding_check(
        self,
        command: BrokerOrderCommand,
        *,
        side: str | None = None,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        role: str = "dry_run",
    ) -> dict[str, Any]:
        return await self._fx_cash_funding(command, side=side, quantity=quantity, price=price, role=role)

    async def _require_fx_cash_funding(
        self,
        command: BrokerOrderCommand,
        *,
        side: str | None = None,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        role: str = "entry",
    ) -> dict[str, Any]:
        check = await self._fx_cash_funding(command, side=side, quantity=quantity, price=price, role=role)
        if check["funding_status"] == "INSUFFICIENT":
            exc = BrokerSafetyError("INSUFFICIENT_SETTLEMENT_CURRENCY", "FX order would exceed real IBKR settlement cash", status_code=422)
            exc.details = check  # type: ignore[attr-defined]
            raise exc
        if check["funding_status"] == "UNKNOWN":
            exc = BrokerSafetyError("FX_ACCOUNT_CAPABILITY_UNKNOWN", "FX account funding capability could not be verified", status_code=503)
            exc.details = check  # type: ignore[attr-defined]
            raise exc
        return check

    async def _fx_cash_funding(
        self,
        command: BrokerOrderCommand,
        *,
        side: str | None = None,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        role: str = "entry",
        cash_overrides: dict[str, Decimal] | None = None,
    ) -> dict[str, Any]:
        base, quote = _fx_pair(command.instrument_id)
        action = (side or command.side).upper()
        qty = Decimal(str(quantity or command.quantity))
        snapshot = await self.account_snapshot(command.account_id)
        account_capability = _fx_account_capability(snapshot)
        available_by_currency = {row.currency.upper(): Decimal(str(row.available_cash)) for row in snapshot.cash}
        if cash_overrides:
            available_by_currency.update({key.upper(): Decimal(str(value)) for key, value in cash_overrides.items()})
        if account_capability["forex_leverage_permitted"] is True:
            return {
                "funding_status": "FUNDED",
                "pair": f"{base}{quote}",
                "action": action,
                "requested_quantity": str(qty),
                "account_capability": account_capability,
                "available_cash_by_currency": {k: str(v) for k, v in available_by_currency.items()},
                "role": role,
            }
        if account_capability["forex_leverage_permitted"] is None:
            return {
                "funding_status": "UNKNOWN",
                "pair": f"{base}{quote}",
                "action": action,
                "requested_quantity": str(qty),
                "account_capability": account_capability,
                "available_cash_by_currency": {k: str(v) for k, v in available_by_currency.items()},
                "role": role,
            }
        required_currency = quote if action == "BUY" else base
        execution_price = Decimal("1")
        if action == "BUY":
            execution_price = Decimal(str(price)) if price is not None else _quote_execution_price(await self.quote(command.instrument_id))
        commission = _decimal_env("FX_FUNDING_COMMISSION_BUFFER", "2.50")
        cash_buffer = _decimal_env("FX_FUNDING_CASH_BUFFER", "1.00")
        slippage_pips = _decimal_env("FX_FUNDING_SLIPPAGE_PIPS", os.getenv("AI_ESTIMATED_SLIPPAGE_PIPS", "0"))
        pip = Decimal("0.01") if quote == "JPY" else Decimal("0.0001")
        slippage_reserve = qty * slippage_pips * pip if action == "BUY" else Decimal("0")
        required_cash = (qty * execution_price if action == "BUY" else qty) + commission + cash_buffer + slippage_reserve
        available_cash = available_by_currency.get(required_currency, Decimal("0"))
        affordable_basis = max(Decimal("0"), available_cash - commission - cash_buffer)
        if action == "BUY":
            affordable_basis = max(Decimal("0"), affordable_basis - slippage_reserve)
            max_affordable = (affordable_basis / execution_price).quantize(Decimal("1"), rounding=ROUND_FLOOR) if execution_price > 0 else Decimal("0")
        else:
            max_affordable = affordable_basis.quantize(Decimal("1"), rounding=ROUND_FLOOR)
        shortfall = max(Decimal("0"), required_cash - available_cash)
        return {
            "funding_status": "FUNDED" if shortfall <= 0 else "INSUFFICIENT",
            "pair": f"{base}{quote}",
            "action": action,
            "requested_quantity": str(qty),
            "required_currency": required_currency,
            "available_cash": str(available_cash),
            "required_cash": str(required_cash.quantize(Decimal("0.000001"))),
            "commission_estimate": str(commission),
            "slippage_reserve": str(slippage_reserve.quantize(Decimal("0.000001"))),
            "cash_buffer": str(cash_buffer),
            "funding_shortfall": str(shortfall.quantize(Decimal("0.000001"))),
            "maximum_affordable_quantity": str(max_affordable),
            "balance_timestamp": snapshot.received_timestamp.isoformat(),
            "account_capability": account_capability,
            "available_cash_by_currency": {k: str(v) for k, v in available_by_currency.items()},
            "role": role,
        }

    async def dry_run_fx5e_acceptance_order(self, *, account_id: str, side: str, quantity: Decimal, order_ref: str) -> dict[str, Any]:
        self._assert_account_allowed(account_id)
        self._ensure_real_ready()
        return self.real_client.dry_run_fx5e_market_order(  # type: ignore[union-attr]
            account_id=account_id,
            side=side,
            quantity=quantity,
            order_ref=order_ref,
        )

    async def cancel_order(self, command: BrokerCancelCommand) -> BrokerCancelReceipt:
        self._assert_account_allowed(command.account_id)
        order = order_book.orders.get(command.canonical_order_id)
        if not order or order.broker_order_id != command.broker_order_id:
            from backend.brokers.errors import BrokerSafetyError

            raise BrokerSafetyError("BROKER_ORDER_NOT_FOUND", "broker order not found", status_code=404)
        order.state = BrokerOrderState.CANCELLED
        order.raw_status = "Cancelled"
        return BrokerCancelReceipt(canonical_order_id=command.canonical_order_id, broker_order_id=command.broker_order_id, state=order.state)

    def _assert_account_allowed(self, account_id: str) -> None:
        if account_id not in self.config.account_allow_list:
            from backend.brokers.errors import BrokerSafetyError

            raise BrokerSafetyError("BROKER_ACCOUNT_NOT_ALLOWED", "broker account is not allow-listed", status_code=403)

    def _ensure_real_ready(self) -> None:
        if not self.real_client or not self.real_client.api_ready:
            raise BrokerCapabilityError("REAL_IBKR_API_NOT_READY", "real TWS API is not ready", status_code=503)

    def _real_account_verified(self) -> bool:
        expected = self.config.expected_account
        accounts = self.real_client.account_rows() if self.real_client else []  # type: ignore[union-attr]
        return bool(expected and any(row.account_id == expected and row.allowed and row.paper_verified for row in accounts))

    def _resolve_real_contract(self, instrument_id: str) -> BrokerContract:
        raw = instrument_id.strip().upper().replace("FX:", "").replace("/", "")
        if len(raw) != 6:
            raise BrokerCapabilityError("UNSUPPORTED_FOREX_CONTRACT", "invalid forex pair", status_code=422)
        base, quote = raw[:3], raw[3:]
        rows = self.real_client.contract_details_rows(base, quote)  # type: ignore[union-attr]
        matches = []
        for row in rows:
            contract = row.get("contract", {})
            valid_exchanges = str(row.get("valid_exchanges") or "")
            if (
                contract.get("security_type") == "CASH"
                and contract.get("symbol") == base
                and contract.get("currency") == quote
                and (contract.get("exchange") == "IDEALPRO" or "IDEALPRO" in valid_exchanges)
            ):
                matches.append(row)
        if not matches:
            raise BrokerCapabilityError("CONTRACT_NOT_FOUND", f"{raw} contractDetails returned no deterministic match", status_code=404)
        unique_con_ids = {int(row["contract"].get("con_id") or 0) for row in matches}
        if len(unique_con_ids) > 1:
            raise BrokerCapabilityError("CONTRACT_AMBIGUOUS", f"{raw} contractDetails returned multiple matches", status_code=422)
        selected = matches[0]
        self._last_contract_details[raw] = selected
        contract = selected["contract"]
        return BrokerContract(
            instrument_id=f"FX:{raw}",
            symbol=base,
            asset_type="FOREX",
            exchange=contract.get("exchange") or "IDEALPRO",
            primary_exchange=contract.get("primary_exchange"),
            currency=quote,
            security_type="CASH",
            local_symbol=contract.get("local_symbol"),
            trading_class=contract.get("trading_class"),
            con_id=int(contract.get("con_id") or 0),
            source="REAL_IBAPI",
            resolution_version="ibapi.contractDetails",
        )


def _fx_pair(instrument_id: str) -> tuple[str, str]:
    symbol = "".join(ch for ch in str(instrument_id or "").upper().replace("FX:", "") if ch.isalpha())
    if len(symbol) != 6:
        raise BrokerSafetyError("FX_ACCOUNT_CAPABILITY_UNKNOWN", f"unsupported FX instrument for funding check: {instrument_id}", status_code=422)
    return symbol[:3], symbol[3:]


def _fx_account_capability(snapshot: BrokerAccountSnapshot) -> dict[str, Any]:
    account_type = str(getattr(snapshot, "account_type", "") or "CASH").upper()
    leverage_env = os.getenv("FX_FOREX_LEVERAGE_PERMITTED", "").strip().lower()
    if leverage_env in {"1", "true", "yes", "on"}:
        leverage_permitted: bool | None = True
    elif leverage_env in {"0", "false", "no", "off"}:
        leverage_permitted = False
    elif account_type in {"MARGIN", "PORTFOLIO_MARGIN"}:
        leverage_permitted = True
    else:
        leverage_permitted = False
    return {
        "account_type": account_type,
        "cash_or_margin_capability": "MARGIN" if leverage_permitted else "CASH",
        "base_currency": next((row.currency for row in snapshot.cash if row.currency), "UNKNOWN"),
        "available_funds": str(snapshot.available_funds),
        "excess_liquidity": str(snapshot.excess_liquidity),
        "forex_leverage_permitted": leverage_permitted,
    }


def _quote_execution_price(quote: BrokerQuote) -> Decimal:
    for value in (quote.ask, quote.last, quote.midpoint, quote.bid):
        if value is not None and Decimal(str(value)) > 0:
            return Decimal(str(value))
    raise BrokerSafetyError("FX_ACCOUNT_CAPABILITY_UNKNOWN", f"fresh executable quote unavailable for {quote.instrument_id}", status_code=503)


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except Exception:
        return Decimal(default)


def _broker_order_from_real(row: dict[str, Any], account_id: str) -> BrokerOrder:
    order = row.get("order", {})
    contract = row.get("contract", {})
    order_id = str(row.get("order_id") or order.get("perm_id") or "unknown")
    state = BrokerOrderState.SUBMITTED
    status = row.get("status") or row.get("state")
    if str(status).lower() in {"filled"}:
        state = BrokerOrderState.FILLED
    elif str(status).lower() in {"cancelled", "canceled"}:
        state = BrokerOrderState.CANCELLED
    return BrokerOrder(
        canonical_order_id=f"IBKR:{order_id}",
        broker_order_id=order_id,
        permanent_id=str(order.get("perm_id") or ""),
        account_id=order.get("account") or account_id,
        instrument_id=f"FX:{contract.get('symbol')}{contract.get('currency')}" if contract.get("security_type") == "CASH" else f"IBKR:{contract.get('con_id')}",
        state=state,
        raw_status=str(status or ""),
        average_price=_dec(row.get("avg_fill_price")),
        filled_quantity=_dec(row.get("filled")),
        remaining_quantity=_dec(row.get("remaining")),
        correlation_id=order.get("order_ref"),
    )


def _broker_execution_from_real(row: dict[str, Any]) -> BrokerExecution:
    execution = row.get("execution", {})
    contract = row.get("contract", {})
    commission = row.get("commission_report", {})
    return BrokerExecution(
        execution_id=execution.get("exec_id") or "unknown",
        canonical_order_id=f"IBKR:{execution.get('order_id')}",
        broker_order_id=str(execution.get("order_id") or ""),
        account_id=execution.get("account") or "",
        instrument_id=f"FX:{contract.get('symbol')}{contract.get('currency')}" if contract.get("security_type") == "CASH" else f"IBKR:{contract.get('con_id')}",
        side=execution.get("side") or "",
        quantity=_dec(execution.get("quantity")),
        price=_dec(execution.get("price")),
        commission=_dec(commission.get("commission")),
        currency=commission.get("currency") or contract.get("currency") or "USD",
    )


def _summary_value(data: dict[str, list[dict[str, str]]], tag: str, *, currency: str | None = None, default: str | None = None) -> str | None:
    rows = data.get(tag) or []
    if currency:
        for row in rows:
            if str(row.get("currency") or "").upper() == currency.upper():
                return row.get("value")
    for row in rows:
        row_currency = str(row.get("currency") or "").upper()
        if not row_currency or row_currency == "BASE":
            return row.get("value")
    return rows[0].get("value") if rows else default


def _summary_cash_balances(data: dict[str, list[dict[str, str]]], *, base_currency: str) -> list[BrokerCashBalance]:
    available_by_currency = {
        str(row.get("currency") or "").upper(): _dec(row.get("value"))
        for row in data.get("AvailableFunds", [])
        if str(row.get("currency") or "").strip()
    }
    balances: list[BrokerCashBalance] = []
    seen: set[str] = set()
    for row in data.get("TotalCashValue", []):
        currency = str(row.get("currency") or base_currency or "USD").upper()
        if currency in seen:
            continue
        seen.add(currency)
        balances.append(
            BrokerCashBalance(
                currency=currency,
                settled_cash=_dec(row.get("value")),
                available_cash=available_by_currency.get(currency, available_by_currency.get(base_currency.upper(), Decimal("0"))),
                source="broker",
            )
        )
    if not balances:
        balances.append(
            BrokerCashBalance(
                currency=base_currency or "USD",
                settled_cash=_dec(_summary_value(data, "TotalCashValue", currency=base_currency)),
                available_cash=_dec(_summary_value(data, "AvailableFunds", currency=base_currency)),
                source="broker",
            )
        )
    return balances


def _ai_auto_paper_max_quantity() -> Decimal:
    try:
        from backend.intelligence.trading.config import ai_trading_config

        return Decimal(str(ai_trading_config().max_position_size_forex))
    except Exception:
        return Decimal("1000")


ibkr_adapter = IBKRPaperAdapter()

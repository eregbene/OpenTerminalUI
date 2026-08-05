from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.brokers import broker_registry
from backend.brokers.errors import BrokerError
from backend.brokers.models import BrokerConnectionState, BrokerContract, BrokerEnvironment, BrokerOrderCommand, BrokerOrderState
from backend.brokers.ibkr.configuration import ibkr_config
from backend.brokers.ibkr.persistence import IbkrAcceptanceDbStore, broker_tables_available
from backend.trading.serialization import model_to_jsonable, read_json, write_json


FX5_EXECUTION_PROVIDER = "IBKR_PAPER"
LOCAL_SIMULATOR_PROVIDER = "LOCAL_SIMULATOR"
SUPPORTED_IBKR_FOREX = ["EURUSD", "GBPUSD", "USDJPY"]
FX5E_APPROVAL_PATH = Path("data/forex_strategies/fx5e_manual_approvals.json")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _id(prefix: str, payload: Any) -> str:
    return f"{prefix}_{_hash(payload)[:16]}"


def mask_account(account_id: str | None) -> str | None:
    if not account_id:
        return None
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return f"{account_id[:2]}***{account_id[-2:]}"


class AcceptanceConnectionState(str):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    ACCOUNT_VERIFIED = "ACCOUNT_VERIFIED"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AccountVerificationRecord(BaseModel):
    verification_id: str
    broker: str = "IBKR"
    account_id_masked: str | None = None
    account_alias: str | None = None
    account_type: str = "UNKNOWN"
    account_mode: str = "UNKNOWN"
    trading_environment: str = "UNVERIFIED"
    server_host: str | None = None
    server_port: int | None = None
    client_id: int
    session_id: str
    account_id_hash: str | None = None
    connected_at: datetime | None = None
    verified_at: datetime | None = None
    verification_source: list[str] = Field(default_factory=list)
    live_execution_allowed: bool = False
    accepted: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)


class ContractVerificationRecord(BaseModel):
    broker: str = "IBKR"
    environment: str = "PAPER"
    canonical_symbol: str
    contract_id: str
    base_currency: str
    quote_currency: str
    security_type: str
    exchange: str
    primary_exchange: str | None = None
    currency: str
    local_symbol: str | None = None
    con_id: int
    trading_class: str | None = None
    minimum_tick: str
    quantity_rules: dict[str, Any] = Field(default_factory=lambda: {"type": "units", "minimum_quantity": "1", "whole_units": True})
    market_rule_ids: list[str] = Field(default_factory=list)
    order_types: list[str] = Field(default_factory=lambda: ["MARKET"])
    verified: bool = False
    verification_source: str = "FIXTURE"
    record_source: str = "UNKNOWN_LEGACY"
    is_quarantined: bool = False
    quarantined_at: datetime | None = None
    quarantine_reason: str | None = None
    test_run_id: str | None = None
    usable_for_real_submission: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    contract_details_received_at: datetime = Field(default_factory=utcnow)
    content_hash: str


class BrokerOrderRecord(BaseModel):
    broker_order_record_id: str
    broker: str = "IBKR"
    environment: str = "PAPER"
    account_id_masked: str | None = None
    candidate_id: str
    risk_decision_id: str | None = None
    oms_intent_id: str | None = None
    canonical_symbol: str
    contract_id: str | None = None
    client_order_id: str
    broker_order_id: str | None = None
    permanent_id: str | None = None
    parent_order_id: str | None = None
    order_reference: str
    order_type: str = "MARKET"
    side: str
    quantity: str
    limit_price: str | None = None
    stop_price: str | None = None
    time_in_force: str = "DAY"
    outside_rth: bool = False
    submitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    last_status_at: datetime | None = None
    broker_status: str = "NOT_SUBMITTED"
    internal_status: str = "CREATED"
    filled_quantity: str = "0"
    remaining_quantity: str = "0"
    average_fill_price: str | None = None
    last_fill_price: str | None = None
    commission: str | None = None
    commission_status: str = "PENDING"
    currency: str = "USD"
    record_source: str = "UNKNOWN_LEGACY"
    is_quarantined: bool = False
    quarantined_at: datetime | None = None
    quarantine_reason: str | None = None
    test_run_id: str | None = None
    broker_session_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    execution_provider: str = FX5_EXECUTION_PROVIDER
    content_hash: str


class BrokerEventRecord(BaseModel):
    event_id: str
    broker_order_record_id: str
    event_type: str
    broker_timestamp: datetime | None = None
    received_at: datetime = Field(default_factory=utcnow)
    sequence: int
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str
    record_source: str = "UNKNOWN_LEGACY"
    is_quarantined: bool = False
    quarantined_at: datetime | None = None
    quarantine_reason: str | None = None
    test_run_id: str | None = None


class ReconciliationRecord(BaseModel):
    reconciliation_id: str
    status: str
    broker: str = "IBKR"
    environment: str = "PAPER"
    account_id_masked: str | None = None
    trade_id: str | None = None
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    scope_metadata: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class OperationalIncident(BaseModel):
    incident_id: str
    severity: str
    code: str
    message: str
    blocking: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Fx5AcceptanceStore:
    def __init__(self, root: Path | str = "data/forex_strategies") -> None:
        self.root = Path(root)
        self.path = self.root / "ibkr_acceptance_store.json"

    def load(self) -> dict[str, Any]:
        data = read_json(self.path)
        return {
            "account_verifications": data.get("account_verifications", {}),
            "contracts": data.get("contracts", {}),
            "broker_orders": data.get("broker_orders", {}),
            "events": data.get("events", []),
            "reconciliations": data.get("reconciliations", []),
            "incidents": data.get("incidents", []),
            "recovery": data.get("recovery", {"status": "IDLE", "last_started_at": None, "last_completed_at": None, "blocking": False}),
        }

    def save(self, data: dict[str, Any]) -> None:
        write_json(self.path, data)

    def upsert_verification(self, row: AccountVerificationRecord) -> None:
        data = self.load()
        data["account_verifications"][row.verification_id] = model_to_jsonable(row)
        self.save(data)

    def upsert_contract(self, row: ContractVerificationRecord) -> None:
        data = self.load()
        data["contracts"][row.canonical_symbol] = model_to_jsonable(row)
        self.save(data)

    def upsert_order(self, row: BrokerOrderRecord) -> None:
        data = self.load()
        data["broker_orders"][row.broker_order_record_id] = model_to_jsonable(row)
        self.save(data)

    def append_event(self, row: BrokerEventRecord) -> None:
        data = self.load()
        data["events"].append(model_to_jsonable(row))
        self.save(data)

    def append_reconciliation(self, row: ReconciliationRecord) -> None:
        data = self.load()
        data["reconciliations"].append(model_to_jsonable(row))
        self.save(data)

    def append_incident(self, row: OperationalIncident) -> None:
        data = self.load()
        data["incidents"].append(model_to_jsonable(row))
        self.save(data)

    def set_recovery(self, status: str, blocking: bool) -> None:
        data = self.load()
        data["recovery"] = {
            "status": status,
            "last_started_at": utcnow().isoformat() if status == "RECOVERY_IN_PROGRESS" else data.get("recovery", {}).get("last_started_at"),
            "last_completed_at": utcnow().isoformat() if status != "RECOVERY_IN_PROGRESS" else None,
            "blocking": blocking,
        }
        self.save(data)


class IbkrPaperAcceptanceService:
    def __init__(self, store: Fx5AcceptanceStore | None = None) -> None:
        self.store = store or self._default_store()

    def _default_store(self):
        if ibkr_config.real_paper_mode:
            if not broker_tables_available():
                raise RuntimeError("IBKR_REAL_PAPER_REQUIRES_DATABASE_STORE")
            return IbkrAcceptanceDbStore()
        return Fx5AcceptanceStore()

    def _load_fx5e(self) -> dict[str, Any]:
        data = read_json(FX5E_APPROVAL_PATH)
        return {"previews": data.get("previews", {})}

    def _save_fx5e(self, data: dict[str, Any]) -> None:
        write_json(FX5E_APPROVAL_PATH, data)

    def _fx5e_precondition_summary(self, *, readonly: dict[str, Any], account_id: str, contract: ContractVerificationRecord, positions: list[Any], open_orders: list[Any]) -> dict[str, Any]:
        eurusd_position = [
            row
            for row in positions
            if str(getattr(row, "instrument_id", "")).upper() == "FX:EURUSD" and Decimal(str(getattr(row, "quantity", "0"))) != 0
        ]
        eurusd_orders = [
            row
            for row in open_orders
            if str(getattr(row, "instrument_id", "")).upper() == "FX:EURUSD" or "FX5E_ACCEPTANCE" in str(getattr(row, "correlation_id", ""))
        ]
        reconciliation_status = ((readonly.get("reconciliation") or {}).get("status") or "").upper()
        return {
            "ibkr_mode_paper": ibkr_config.mode == "PAPER",
            "ibkr_allow_live_false": os.getenv("IBKR_ALLOW_LIVE", "false").strip().lower() not in {"1", "true", "yes", "on"},
            "live_trading_disabled": not ibkr_config.live_trading_enabled,
            "client_id_120": ibkr_config.client_id == 120,
            "api_ready": bool(readonly.get("api_ready", True)),
            "broker_connected": bool(readonly.get("connection_successful")),
            "account_verified": bool(readonly.get("account_verified")),
            "active_request_count_zero": int(readonly.get("active_request_count", 0) or 0) == 0,
            "reconciliation_clean": reconciliation_status in {"MATCHED", "MATCHED_EMPTY"},
            "emergency_disable_false": True,
            "eurusd_contract_conid": contract.con_id == 12087792,
            "eurusd_contract_cash": contract.security_type == "CASH",
            "eurusd_contract_idealpro": contract.exchange == "IDEALPRO",
            "eurusd_contract_usd": contract.currency == "USD",
            "no_unexpected_eurusd_position": not eurusd_position,
            "no_unexpected_eurusd_open_order": not eurusd_orders,
            "no_unresolved_local_submission": not any(row.get("broker_status") == "SUBMISSION_STATUS_UNKNOWN" for row in self.store.load()["broker_orders"].values()),
            "account_id": account_id,
            "initial_eurusd_position": [row.model_dump(mode="json") for row in eurusd_position],
            "initial_eurusd_open_orders": [row.model_dump(mode="json") for row in eurusd_orders],
            "reconciliation_status": reconciliation_status,
        }

    async def create_fx5e_preview(self, *, side: str = "BUY", quantity: str = "1000") -> dict[str, Any]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            return {"status": "BLOCKED", "blocking_reasons": ["INVALID_SIDE"]}
        qty = Decimal(quantity)
        if qty <= 0 or qty > Decimal("1000"):
            return {"status": "BLOCKED", "blocking_reasons": ["INVALID_FX5E_QUANTITY"]}
        readonly = await self.read_only_acceptance()
        verification = self._latest_verification()
        if not verification or not verification.accepted:
            return {"status": "BLOCKED", "blocking_reasons": ["ACCOUNT_NOT_VERIFIED"], "read_only_acceptance": readonly}
        account_id = ibkr_config.expected_account or ""
        contract = await self.verify_contract("EURUSD")
        adapter = broker_registry.get("ibkr")
        positions = await adapter.positions(account_id)
        open_orders = await adapter.open_orders(account_id)
        preconditions = self._fx5e_precondition_summary(readonly=readonly, account_id=account_id, contract=contract, positions=positions, open_orders=open_orders)
        blocking = [key for key, value in preconditions.items() if key not in {"account_id", "initial_eurusd_position", "initial_eurusd_open_orders", "reconciliation_status"} and value is not True]
        order_reference = f"FX5E_ACCEPTANCE_{uuid4().hex[:12]}"
        preview_id = _id("fx5epreview", [order_reference, utcnow()])
        approval_token = uuid4().hex
        expires_at = utcnow().timestamp() + 300
        rate = Decimal("1.0830")
        stop_loss = Decimal("1.0810") if side == "BUY" else Decimal("1.0850")
        take_profit = Decimal("1.0870") if side == "BUY" else Decimal("1.0790")
        max_loss = (abs(rate - stop_loss) * qty).quantize(Decimal("0.01"))
        preview = {
            "preview_id": preview_id,
            "status": "READY" if not blocking else "BLOCKED",
            "blocking_reasons": blocking,
            "symbol": "EUR/USD",
            "side": side,
            "order_type": "MARKET",
            "quantity": str(qty),
            "estimated_rate": str(rate),
            "estimated_notional_usd": str((rate * qty).quantize(Decimal("0.01"))),
            "estimated_maximum_loss_usd": str(max_loss),
            "stop_loss": str(stop_loss),
            "take_profit": str(take_profit),
            "risk_percentage": "0.01",
            "account_mode": "PAPER",
            "masked_account": verification.account_id_masked,
            "account_id": account_id,
            "order_reference": order_reference,
            "approval_token": approval_token,
            "approval_expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "approval_status": "PENDING",
            "submitted": False,
            "preconditions": preconditions,
        }
        if hasattr(adapter, "dry_run_fx5e_acceptance_order"):
            dry_run = await adapter.dry_run_fx5e_acceptance_order(account_id=account_id, side=side, quantity=qty, order_ref=order_reference)
            preview["compatibility"] = dry_run
            if dry_run.get("status") != "COMPATIBLE":
                preview["status"] = "BLOCKED"
                preview["blocking_reasons"] = [*blocking, "IBKR_UNSUPPORTED_ORDER_ATTRIBUTE"]
        data = self._load_fx5e()
        data["previews"][preview_id] = {**preview, "approval_token_hash": _hash(approval_token), "approval_token": None}
        self._save_fx5e(data)
        return preview

    def approve_fx5e_preview(self, *, preview_id: str, approval_token: str) -> dict[str, Any]:
        data = self._load_fx5e()
        preview = data["previews"].get(preview_id)
        if not preview:
            return {"status": "BLOCKED", "blocking_reasons": ["PREVIEW_NOT_FOUND"]}
        if preview.get("submitted"):
            return {"status": "BLOCKED", "blocking_reasons": ["ORDER_ALREADY_SUBMITTED"]}
        if preview.get("approval_status") == "APPROVED":
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_ALREADY_USED"]}
        if _hash(approval_token) != preview.get("approval_token_hash"):
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_TOKEN_INVALID"]}
        if datetime.fromisoformat(preview["approval_expires_at"]) < utcnow():
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_EXPIRED"]}
        preview["approval_status"] = "APPROVED"
        preview["approved_at"] = utcnow().isoformat()
        data["previews"][preview_id] = preview
        self._save_fx5e(data)
        return {"status": "APPROVED", "preview_id": preview_id, "order_reference": preview["order_reference"], "approval_expires_at": preview["approval_expires_at"]}

    async def submit_fx5e_approved_preview(self, *, preview_id: str) -> dict[str, Any]:
        data = self._load_fx5e()
        preview = data["previews"].get(preview_id)
        if not preview:
            return {"status": "BLOCKED", "blocking_reasons": ["PREVIEW_NOT_FOUND"]}
        if preview.get("approval_status") != "APPROVED":
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_MISSING"]}
        if preview.get("approval_consumed_at"):
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_ALREADY_CONSUMED"]}
        if preview.get("submitted"):
            return {"status": "BLOCKED", "blocking_reasons": ["ORDER_ALREADY_SUBMITTED"]}
        if datetime.fromisoformat(preview["approval_expires_at"]) < utcnow():
            return {"status": "BLOCKED", "blocking_reasons": ["APPROVAL_EXPIRED"]}
        blocking = await self._fx5e_runtime_blockers(preview)
        if blocking:
            return {"status": "BLOCKED", "blocking_reasons": blocking}
        adapter = broker_registry.get("ibkr")
        command = BrokerOrderCommand(
            canonical_order_id=preview_id,
            account_id=preview["account_id"],
            instrument_id="FX:EURUSD",
            side=preview["side"],
            order_type="MARKET",
            time_in_force="DAY",
            quantity=Decimal(str(preview["quantity"])),
            approved_quantity=Decimal(str(preview["quantity"])),
            risk_evaluation_id=f"risk_{preview_id}",
            idempotency_key=preview_id,
            correlation_id=preview["order_reference"],
            user_approval=True,
        )
        preview["approval_consumed_at"] = utcnow().isoformat()
        preview["submitted"] = True
        preview["status"] = "SUBMITTING"
        data["previews"][preview_id] = preview
        self._save_fx5e(data)
        try:
            submission = await adapter.submit_fx5e_acceptance_order(command)
        except Exception as exc:
            record = self.record_order(
                candidate_id=preview_id,
                oms_intent_id=preview_id,
                account_id=preview["account_id"],
                symbol="EURUSD",
                side=preview["side"],
                quantity=preview["quantity"],
                order_reference=preview["order_reference"],
                record_source="REAL_IBKR",
                broker_status="SUBMISSION_STATUS_UNKNOWN",
                internal_status="SUBMISSION_UNKNOWN",
                event_type="SUBMISSION_STATUS_UNKNOWN",
            )
            preview["status"] = "SUBMISSION_UNKNOWN"
            data["previews"][preview_id] = preview
            self._save_fx5e(data)
            return {"status": "SUBMISSION_UNKNOWN", "error": f"{type(exc).__name__}: {exc}", "order": record.model_dump(mode="json")}
        lifecycle = self._persist_fx5e_submission(preview=preview, submission=submission, close=False)
        preview["status"] = lifecycle["order"]["internal_status"]
        preview["broker_order_record_id"] = lifecycle["order"]["broker_order_record_id"]
        data["previews"][preview_id] = preview
        self._save_fx5e(data)
        return {"status": lifecycle["order"]["internal_status"], "preview_id": preview_id, **lifecycle}

    async def close_fx5e_acceptance_trade(self, *, preview_id: str, manual_confirmation: bool) -> dict[str, Any]:
        if not manual_confirmation:
            return {"status": "BLOCKED", "blocking_reasons": ["MANUAL_CLOSE_CONFIRMATION_REQUIRED"]}
        data = self._load_fx5e()
        preview = data["previews"].get(preview_id)
        if not preview or not preview.get("broker_order_record_id"):
            return {"status": "BLOCKED", "blocking_reasons": ["ENTRY_NOT_FOUND"]}
        adapter = broker_registry.get("ibkr")
        positions = await adapter.positions(preview["account_id"])
        eurusd = [row for row in positions if row.instrument_id.upper() == "FX:EURUSD" and row.quantity != 0]
        if not eurusd:
            return {"status": "FLAT", "final_position": "0"}
        position_qty = eurusd[0].quantity
        close_qty = abs(position_qty)
        if close_qty <= 0 or close_qty > Decimal(str(preview["quantity"])):
            return {"status": "BLOCKED", "blocking_reasons": ["CLOSE_QUANTITY_INVALID"], "position_quantity": str(position_qty)}
        close_side = "SELL" if position_qty > 0 else "BUY"
        command = BrokerOrderCommand(
            canonical_order_id=f"{preview_id}_close",
            account_id=preview["account_id"],
            instrument_id="FX:EURUSD",
            side=close_side,
            order_type="MARKET",
            time_in_force="DAY",
            quantity=close_qty,
            approved_quantity=close_qty,
            risk_evaluation_id=f"risk_{preview_id}_close",
            idempotency_key=f"{preview_id}_close",
            correlation_id=f"{preview['order_reference']}_CLOSE",
            user_approval=True,
        )
        submission = await adapter.submit_fx5e_acceptance_order(command)
        close_preview = {**preview, "side": close_side, "quantity": str(close_qty), "order_reference": command.correlation_id}
        lifecycle = self._persist_fx5e_submission(preview=close_preview, submission=submission, close=True)
        return {"status": lifecycle["order"]["internal_status"], **lifecycle}

    async def _fx5e_runtime_blockers(self, preview: dict[str, Any]) -> list[str]:
        readonly = await self.read_only_acceptance()
        contract = await self.verify_contract("EURUSD")
        adapter = broker_registry.get("ibkr")
        positions = await adapter.positions(preview["account_id"])
        open_orders = await adapter.open_orders(preview["account_id"])
        preconditions = self._fx5e_precondition_summary(readonly=readonly, account_id=preview["account_id"], contract=contract, positions=positions, open_orders=open_orders)
        return [key for key, value in preconditions.items() if key not in {"account_id", "initial_eurusd_position", "initial_eurusd_open_orders", "reconciliation_status"} and value is not True]

    def _persist_fx5e_submission(self, *, preview: dict[str, Any], submission: dict[str, Any], close: bool) -> dict[str, Any]:
        statuses = submission.get("statuses") or []
        errors = submission.get("errors") or []
        latest = statuses[-1] if statuses else {}
        open_order = submission.get("open_order") or {}
        order_payload = open_order.get("order") or {}
        executions = submission.get("executions") or []
        commissions = submission.get("commission_reports") or []
        filled = str(latest.get("filled") or "0")
        remaining = str(latest.get("remaining") or preview["quantity"])
        avg_price = str(latest.get("avg_fill_price") or "0")
        status = str(latest.get("status") or submission.get("submission_state") or "ACKNOWLEDGED")
        fully_filled = Decimal(filled) > 0 and Decimal(remaining) == 0
        if fully_filled:
            status = "FILLED"
        elif errors:
            status = "REJECTED"
        internal_status = (
            "REJECTED"
            if status.upper() in {"REJECTED", "INACTIVE", "CANCELLED", "CANCELED"}
            else "SUBMISSION_UNKNOWN"
            if status.upper() == "SUBMISSION_UNKNOWN"
            else "FILLED"
            if status.upper() == "FILLED" or fully_filled
            else "SUBMITTED"
        )
        commission = commissions[-1].get("commission") if commissions else None
        record = self.record_order(
            candidate_id=f"{preview['preview_id']}_{'close' if close else 'entry'}",
            oms_intent_id=preview["preview_id"],
            account_id=preview["account_id"],
            symbol="EURUSD",
            side=preview["side"],
            quantity=preview["quantity"],
            broker_order_id=str(submission.get("order_id") or open_order.get("order_id") or ""),
            permanent_id=str(order_payload.get("perm_id")) if order_payload.get("perm_id") else None,
            order_reference=preview["order_reference"],
            record_source="REAL_IBKR",
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_fill_price=avg_price,
            commission=commission,
            commission_status="RECEIVED" if commission is not None else "UNAVAILABLE",
            broker_status=status,
            internal_status=internal_status,
            event_type="ORDER_ACKNOWLEDGED",
        )
        if open_order:
            self._event(record.broker_order_record_id, "openOrder", open_order)
        for row in statuses:
            self._event(record.broker_order_record_id, "orderStatus", row)
        for row in executions:
            self._event(record.broker_order_record_id, "execDetails", row)
        for row in commissions:
            self._event(record.broker_order_record_id, "commissionReport", row)
        for row in errors:
            self._event(record.broker_order_record_id, "error", row)
        return {
            "order": record.model_dump(mode="json"),
            "broker_callbacks": {
                "openOrder": bool(open_order),
                "orderStatus": len(statuses),
                "execDetails": len(executions),
                "commissionReport": len(commissions),
                "error": len(errors),
            },
            "executions": [(row.get("execution") or {}).get("exec_id") for row in executions],
            "commission": commission,
        }

    async def status(self) -> dict[str, Any]:
        adapter = broker_registry.get("ibkr")
        health = await adapter.health()
        latest_verification = self._latest_verification()
        return {
            "broker": "IBKR",
            "environment": "PAPER",
            "adapter_mode": ibkr_config.adapter_mode,
            "state_source": getattr(self.store, "source", "LEGACY_FIXTURE_FILE"),
            "connection_state": self._acceptance_state(health.connection_state.value, latest_verification),
            "account_verified": bool(latest_verification and latest_verification.accepted),
            "masked_account_id": latest_verification.account_id_masked if latest_verification else None,
            "market_data_state": self._market_data_status(),
            "server_time": utcnow().isoformat(),
            "latency_ms": None,
            "last_reconciliation": self._last_reconciliation(),
            "reconciliation_status": (self._last_reconciliation() or {}).get("status", "UNKNOWN"),
            "emergency_disable": "ACCOUNT_SCOPED",
            "recovery": self.store.load()["recovery"],
            "fixture_mode": ibkr_config.mode == "FIXTURE" or bool(getattr(adapter.config, "simulated", True)),
            "simulated_adapter": bool(getattr(adapter.config, "simulated", True)),
            "real_submission_enabled": ibkr_config.real_paper_mode and bool(latest_verification and latest_verification.accepted),
        }

    async def connect_and_verify(self) -> AccountVerificationRecord:
        adapter = broker_registry.get("ibkr")
        config = getattr(adapter, "config", ibkr_config)
        try:
            health = await adapter.connect()
            accounts = await adapter.accounts()
        except BrokerError as exc:
            record = AccountVerificationRecord(
                verification_id=_id("ibkracct", ["connect_failed", exc.code, utcnow()]),
                account_mode="UNKNOWN",
                trading_environment="UNVERIFIED",
                server_host=config.host,
                server_port=config.port,
                client_id=config.client_id,
                session_id=_id("ibkrsession", ["connect_failed", utcnow()]),
                connected_at=utcnow(),
                verification_source=["broker_connect"],
                accepted=False,
                rejection_reasons=[exc.code],
            )
            self.store.upsert_verification(record)
            self._incident("CRITICAL", exc.code, exc.message, True)
            return record
        reasons: list[str] = []
        accepted = False
        selected = None
        expected = config.expected_account
        if expected:
            matches = [row for row in accounts if row.account_id == expected]
            selected = matches[0] if len(matches) == 1 else None
            if not selected:
                reasons.append("ACCOUNT_MISMATCH")
        elif len(accounts) == 1:
            selected = accounts[0]
        elif len(accounts) > 1:
            reasons.append("MULTIPLE_ACCOUNTS_AMBIGUOUS")
        if not selected:
            if not reasons:
                reasons.append("ACCOUNT_UNKNOWN")
        else:
            if selected.broker.upper() != "IBKR":
                reasons.append("BROKER_MISMATCH")
            if selected.environment != BrokerEnvironment.PAPER:
                reasons.append("LIVE_ACCOUNT_DETECTED" if selected.environment == BrokerEnvironment.LIVE else "ACCOUNT_UNKNOWN")
            if not selected.paper_verified:
                reasons.append("ACCOUNT_VERIFICATION_TIMEOUT" if config.real_paper_mode else "AMBIGUOUS_ACCOUNT")
            if selected.account_id not in config.account_allow_list:
                reasons.append("ACCOUNT_NOT_ALLOWLISTED")
            if config.live_trading_enabled:
                reasons.append("LIVE_ACCOUNT_DETECTED")
            accepted = not reasons
        payload = {
            "account": selected.account_id if selected else "none",
            "host": config.host,
            "port": config.port,
            "client": config.client_id,
            "session": health.session_start,
        }
        record = AccountVerificationRecord(
            verification_id=_id("ibkracct", payload),
            account_id_masked=mask_account(selected.account_id if selected else None),
            account_id_hash=_hash(selected.account_id if selected else "none"),
            account_alias=selected.alias if selected else None,
            account_type=selected.account_type if selected else "UNKNOWN",
            account_mode=selected.environment.value if selected else "UNKNOWN",
            trading_environment=selected.environment.value if selected else "UNVERIFIED",
            server_host=config.host,
            server_port=config.port,
            client_id=config.client_id,
            session_id=_id("ibkrsession", payload),
            connected_at=health.session_start,
            verified_at=utcnow() if accepted else None,
            verification_source=["broker_accounts", "account_summary", "allowlist", "paper_verified_flag", "live_execution_flag"],
            live_execution_allowed=config.live_trading_enabled,
            accepted=accepted,
            rejection_reasons=reasons,
        )
        self.store.upsert_verification(record)
        if not accepted:
            self._incident("CRITICAL", reasons[0] if reasons else "ACCOUNT_VERIFICATION_FAILED", "IBKR paper account verification failed", True)
        return record

    async def disconnect(self) -> dict[str, Any]:
        health = await broker_registry.get("ibkr").disconnect()
        return health.model_dump(mode="json")

    async def verify_contract(self, symbol: str) -> ContractVerificationRecord:
        symbol = symbol.upper()
        reasons: list[str] = []
        if symbol == "XAUUSD":
            reasons.append("CONTRACT_UNAVAILABLE")
        if symbol not in SUPPORTED_IBKR_FOREX:
            reasons.append("CONTRACT_UNRESOLVED")
        contract: BrokerContract | None = None
        if not reasons:
            try:
                contract = await broker_registry.get("ibkr").resolve_contract(f"FX:{symbol}")
            except BrokerError:
                reasons.append("CONTRACT_UNRESOLVED")
        if contract and (contract.security_type != "CASH" or contract.exchange != "IDEALPRO"):
            reasons.append("CONTRACT_AMBIGUOUS")
        base, quote = symbol[:3], symbol[3:]
        adapter = broker_registry.get("ibkr")
        details = getattr(adapter, "_last_contract_details", {}).get(symbol, {}) if contract else {}
        payload = {"symbol": symbol, "contract": contract.model_dump(mode="json") if contract else None, "reasons": reasons}
        real_source = ibkr_config.real_paper_mode and not bool(getattr(broker_registry.get("ibkr").config, "simulated", True))
        record = ContractVerificationRecord(
            canonical_symbol=symbol,
            contract_id=f"IBKR:PAPER:FX:{symbol}:v1",
            base_currency=base,
            quote_currency=quote,
            security_type=contract.security_type if contract else "UNKNOWN",
            exchange=contract.exchange if contract else "UNKNOWN",
            primary_exchange=contract.primary_exchange if contract else None,
            currency=contract.currency if contract else quote,
            local_symbol=contract.local_symbol if contract else None,
            con_id=contract.con_id if contract else 0,
            trading_class=contract.trading_class if contract else None,
            minimum_tick=str(details.get("minimum_tick") or ("0.00001" if "JPY" not in symbol else "0.001")),
            market_rule_ids=list(details.get("market_rule_ids") or []),
            verified=not reasons,
            verification_source="REAL_IBKR_PAPER" if real_source and not reasons else "FIXTURE",
            record_source="REAL_IBKR" if real_source and not reasons else "FIXTURE_IBKR",
            usable_for_real_submission=real_source and not reasons,
            rejection_reasons=reasons,
            content_hash=_hash(payload),
        )
        self.store.upsert_contract(record)
        return record

    async def contracts(self) -> list[ContractVerificationRecord]:
        for symbol in SUPPORTED_IBKR_FOREX + ["XAUUSD"]:
            if symbol not in self.store.load()["contracts"]:
                await self.verify_contract(symbol)
        return [ContractVerificationRecord.model_validate(row) for row in self.store.load()["contracts"].values()]

    def pre_submit_guard(self, *, candidate_id: str, oms_intent_id: str, account_id: str, symbol: str, order_type: str, quantity: str) -> list[str]:
        reasons: list[str] = []
        verification = self._latest_verification()
        if not verification or not verification.accepted:
            reasons.append("ACCOUNT_NOT_VERIFIED")
        if self.store.load()["recovery"].get("blocking"):
            reasons.append("RECOVERY_IN_PROGRESS")
        if symbol == "XAUUSD":
            reasons.append("CONTRACT_UNAVAILABLE")
        contract = self.store.load()["contracts"].get(symbol)
        if not contract or not contract.get("verified"):
            reasons.append("CONTRACT_UNRESOLVED")
        elif ibkr_config.real_paper_mode and contract.get("verification_source") not in {"REAL_IBKR_PAPER", "REAL_IBKR"}:
            reasons.append("FIXTURE_CONTRACT_NOT_EXECUTABLE")
        if order_type != "MARKET":
            reasons.append("UNSUPPORTED_ORDER_TYPE")
        try:
            if float(quantity) <= 0:
                reasons.append("INVALID_QUANTITY")
        except ValueError:
            reasons.append("INVALID_QUANTITY")
        order_reference = _hash([candidate_id, oms_intent_id, mask_account(account_id), "PAPER"])
        if any(row.get("order_reference") == order_reference for row in self.store.load()["broker_orders"].values()):
            reasons.append("DUPLICATE_ORDER_REFERENCE")
        return reasons

    def record_unknown_submission(self, *, candidate_id: str, oms_intent_id: str, account_id: str, symbol: str, side: str, quantity: str) -> BrokerOrderRecord:
        return self.record_order(
            candidate_id=candidate_id,
            oms_intent_id=oms_intent_id,
            account_id=account_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            broker_status="SUBMISSION_STATUS_UNKNOWN",
            internal_status="SUBMISSION_STATUS_UNKNOWN",
            event_type="SUBMISSION_STATUS_UNKNOWN",
        )

    def record_order(
        self,
        *,
        candidate_id: str,
        oms_intent_id: str,
        account_id: str,
        symbol: str,
        side: str,
        quantity: str,
        broker_order_id: str | None = None,
        permanent_id: str | None = None,
        order_reference: str | None = None,
        record_source: str = "UNKNOWN_LEGACY",
        filled_quantity: str = "0",
        remaining_quantity: str | None = None,
        average_fill_price: str | None = None,
        commission: str | None = None,
        commission_status: str = "PENDING",
        broker_status: str = "ACKNOWLEDGED",
        internal_status: str = "SUBMITTED",
        event_type: str = "ORDER_ACKNOWLEDGED",
    ) -> BrokerOrderRecord:
        contract = self.store.load()["contracts"].get(symbol) or {}
        order_reference = order_reference or _hash([candidate_id, oms_intent_id, mask_account(account_id), "PAPER"])
        payload = {"candidate_id": candidate_id, "oms_intent_id": oms_intent_id, "symbol": symbol, "quantity": quantity, "order_reference": order_reference}
        record = BrokerOrderRecord(
            broker_order_record_id=_id("ibkrorder", payload),
            account_id_masked=mask_account(account_id),
            candidate_id=candidate_id,
            oms_intent_id=oms_intent_id,
            canonical_symbol=symbol,
            contract_id=contract.get("contract_id"),
            client_order_id=_id("clientorder", payload),
            broker_order_id=broker_order_id,
            permanent_id=permanent_id,
            order_reference=order_reference,
            side=side,
            quantity=quantity,
            submitted_at=utcnow(),
            acknowledged_at=utcnow() if broker_status == "ACKNOWLEDGED" else None,
            last_status_at=utcnow(),
            broker_status=broker_status,
            internal_status=internal_status,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity if remaining_quantity is not None else quantity,
            average_fill_price=average_fill_price,
            commission=commission,
            commission_status=commission_status,
            record_source=record_source,
            content_hash=_hash(payload),
        )
        self.store.upsert_order(record)
        self._event(record.broker_order_record_id, event_type, {"broker_status": broker_status, "symbol": symbol, "quantity": quantity})
        return record

    def reconcile(self, trade_id: str | None = None) -> ReconciliationRecord:
        data = self.store.load()
        latest = self._latest_verification()
        active_masked_account = latest.account_id_masked if latest else None
        real_scope = ibkr_config.real_paper_mode
        orders = list(data["broker_orders"].values())
        included_orders: list[dict[str, Any]] = []
        excluded_fixture = 0
        excluded_quarantined = 0
        excluded_other_account = 0
        excluded_other_environment = 0
        excluded_other_source = 0
        unknown_legacy = 0
        for row in orders:
            source = row.get("record_source") or "UNKNOWN_LEGACY"
            if source in {"FIXTURE_IBKR", "TEST_SEED", "LOCAL_SIMULATOR"}:
                excluded_fixture += 1
            if row.get("is_quarantined"):
                excluded_quarantined += 1
            if row.get("is_quarantined"):
                continue
            if source in {"FIXTURE_IBKR", "TEST_SEED", "LOCAL_SIMULATOR"}:
                continue
            if real_scope and row.get("environment") != "PAPER":
                excluded_other_environment += 1
                continue
            if active_masked_account and row.get("account_id_masked") and row.get("account_id_masked") != active_masked_account:
                excluded_other_account += 1
                continue
            if real_scope and source != "REAL_IBKR":
                unknown_legacy += 1
                excluded_other_source += 1
                continue
            included_orders.append(row)
        mismatches: list[dict[str, Any]] = []
        for row in included_orders:
            if row.get("broker_status") == "SUBMISSION_STATUS_UNKNOWN":
                mismatches.append({"status": "UNKNOWN", "entity_id": row["broker_order_record_id"], "detail": "submission result unknown; reconcile before retry"})
            if row.get("commission_status") == "PENDING" and row.get("internal_status") in {"FILLED", "SUBMITTED"}:
                mismatches.append({"status": "MISSING_COMMISSION", "entity_id": row["broker_order_record_id"], "detail": "commission report pending"})
        if real_scope and unknown_legacy:
            mismatches.append({"status": "UNKNOWN_LEGACY", "entity_id": "broker_orders", "detail": f"{unknown_legacy} non-quarantined legacy broker order(s) require provenance classification"})
        blocking = any(item["status"] in {"UNKNOWN", "ACCOUNT_MISMATCH", "MISSING_PROTECTIVE_ORDER"} for item in mismatches)
        if real_scope and unknown_legacy:
            blocking = True
        status = "MATCHED_EMPTY" if not mismatches and not included_orders else "MATCHED" if not mismatches else "UNKNOWN" if blocking else "MISSING_COMMISSION"
        scope_metadata = {
            "active_source": "REAL_IBKR" if real_scope else "FIXTURE_IBKR",
            "adapter_mode": ibkr_config.adapter_mode,
            "active_account_masked": active_masked_account,
            "included_local_record_count": len(included_orders),
            "excluded_fixture_count": excluded_fixture,
            "excluded_quarantined_count": excluded_quarantined,
            "excluded_other_account_count": excluded_other_account,
            "excluded_other_environment_count": excluded_other_environment,
            "excluded_other_source_count": excluded_other_source,
            "broker_order_count": len(getattr(broker_registry.get("ibkr"), "_last_open_orders", []) or []),
            "broker_execution_count": len(getattr(broker_registry.get("ibkr"), "_last_executions", []) or []),
        }
        record = ReconciliationRecord(reconciliation_id=_id("ibkrrecon", [trade_id, utcnow()]), status=status, trade_id=trade_id, mismatches=mismatches, scope_metadata=scope_metadata, blocking=blocking, account_id_masked=active_masked_account)
        self.store.append_reconciliation(record)
        if blocking:
            self._incident("HIGH", "RECONCILIATION_BLOCKING", "IBKR reconciliation produced blocking mismatches", True)
        return record

    def recovery_start(self) -> dict[str, Any]:
        self.store.set_recovery("RECOVERY_IN_PROGRESS", True)
        self._incident("INFO", "RECOVERY_IN_PROGRESS", "Recovering IBKR paper state; approvals blocked", True)
        return self.store.load()["recovery"]

    def recovery_complete(self) -> dict[str, Any]:
        recon = self.reconcile()
        self.store.set_recovery("RECOVERY_COMPLETE" if not recon.blocking else "RECOVERY_FAILED", recon.blocking)
        return self.store.load()["recovery"]

    def orders(self) -> list[BrokerOrderRecord]:
        return [BrokerOrderRecord.model_validate(row) for row in self.store.load()["broker_orders"].values()]

    def events(self, order_id: str | None = None) -> list[BrokerEventRecord]:
        rows = [BrokerEventRecord.model_validate(row) for row in self.store.load()["events"]]
        return [row for row in rows if order_id is None or row.broker_order_record_id == order_id]

    def reconciliations(self) -> list[ReconciliationRecord]:
        return [ReconciliationRecord.model_validate(row) for row in self.store.load()["reconciliations"]]

    def incidents(self) -> list[OperationalIncident]:
        return [OperationalIncident.model_validate(row) for row in self.store.load()["incidents"]]

    def executions(self, order_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.load().get("executions", [])
        return [row for row in rows if order_id is None or row.get("broker_order_id") == order_id]

    async def read_only_acceptance(self) -> dict[str, Any]:
        verification = await self.connect_and_verify()
        result: dict[str, Any] = {
            "adapter_mode": ibkr_config.adapter_mode,
            "state_source": getattr(self.store, "source", "LEGACY_FIXTURE_FILE"),
            "provenance": {
                "connection": "NOT_RUN",
                "accounts": "NOT_RUN",
                "server_time": "NOT_RUN",
                "account_summary": "NOT_RUN",
                "positions": "NOT_RUN",
                "open_orders": "NOT_RUN",
                "completed_orders": "NOT_RUN",
                "executions": "NOT_RUN",
                "contracts": {},
            },
            "connection_successful": verification.accepted,
            "account_verified": verification.accepted,
            "masked_account_id": verification.account_id_masked,
            "server_time_received": verification.accepted,
            "account_summary_received": False,
            "positions_received": False,
            "open_orders_received": False,
            "completed_orders_received": False,
            "executions_received": False,
            "market_data_mode": self._market_data_status(),
            "contracts": {},
            "reconciliation": None,
            "blocking_reasons": list(verification.rejection_reasons),
        }
        if not verification.accepted:
            return result
        adapter = broker_registry.get("ibkr")
        diagnostics = {}
        real_client = getattr(adapter, "real_client", None)
        if real_client and hasattr(real_client, "readiness_diagnostics"):
            diagnostics = real_client.readiness_diagnostics()
        result["api_ready"] = bool(diagnostics.get("api_ready", verification.accepted))
        result["active_request_count"] = int(diagnostics.get("pending_requests", 0) or 0)
        result["provenance"]["connection"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
        result["provenance"]["accounts"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
        result["provenance"]["server_time"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
        account_id = ibkr_config.expected_account or (ibkr_config.account_allow_list[0] if ibkr_config.account_allow_list else "")
        try:
            result["account_summary_received"] = bool(await adapter.account_snapshot(account_id))
            result["provenance"]["account_summary"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
            result["positions_received"] = isinstance(await adapter.positions(account_id), list)
            result["provenance"]["positions"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
            result["open_orders_received"] = isinstance(await adapter.open_orders(account_id), list)
            result["provenance"]["open_orders"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
            completed_status = "UNSUPPORTED"
            if hasattr(adapter, "completed_orders"):
                completed_status, _completed = await adapter.completed_orders(account_id)
            result["completed_orders_received"] = completed_status in {"REAL_IBAPI", "FIXTURE"}
            result["provenance"]["completed_orders"] = completed_status
            result["executions_received"] = isinstance(await adapter.executions(account_id), list)
            result["provenance"]["executions"] = "REAL_IBAPI" if ibkr_config.real_paper_mode else "FIXTURE"
            for symbol in SUPPORTED_IBKR_FOREX:
                contract = await self.verify_contract(symbol)
                result["contracts"][symbol] = contract.model_dump(mode="json")
                result["provenance"]["contracts"][symbol] = "REAL_IBAPI" if contract.verification_source in {"REAL_IBKR_PAPER", "REAL_IBKR"} else contract.verification_source
            recon = self.reconcile()
            result["reconciliation"] = recon.model_dump(mode="json")
            if recon.blocking:
                result["blocking_reasons"].append("RECONCILIATION_BLOCKING")
        except BrokerError as exc:
            result["blocking_reasons"].append(exc.code)
            self._incident("HIGH", exc.code, exc.message, True)
        return result

    def _latest_verification(self) -> AccountVerificationRecord | None:
        rows = [AccountVerificationRecord.model_validate(row) for row in self.store.load()["account_verifications"].values()]
        return sorted(rows, key=lambda row: row.verified_at or row.connected_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0] if rows else None

    def _last_reconciliation(self) -> dict[str, Any] | None:
        rows = self.store.load()["reconciliations"]
        return rows[-1] if rows else None

    def _market_data_status(self) -> str:
        mode = str(ibkr_config.market_data_mode).upper()
        return {
            "REALTIME": "IBKR_LIVE",
            "FROZEN": "IBKR_FROZEN",
            "DELAYED": "IBKR_DELAYED",
            "DELAYED_FROZEN": "IBKR_DELAYED_FROZEN",
        }.get(mode, "UNAVAILABLE")

    def _acceptance_state(self, state: str, verification: AccountVerificationRecord | None) -> str:
        if verification and verification.accepted:
            return "ACCOUNT_VERIFIED"
        if state == BrokerConnectionState.CONNECTED.value:
            return "CONNECTED"
        if state == BrokerConnectionState.RECONNECTING.value:
            return "RECONNECTING"
        if state in {BrokerConnectionState.BLOCKED.value, BrokerConnectionState.FAILED.value}:
            return state
        return "DISCONNECTED"

    def _event(self, order_id: str, event_type: str, payload: dict[str, Any]) -> None:
        sequence = len([row for row in self.store.load()["events"] if row.get("broker_order_record_id") == order_id]) + 1
        self.store.append_event(BrokerEventRecord(event_id=_id("ibkrevent", [order_id, event_type, sequence, payload]), broker_order_record_id=order_id, event_type=event_type, sequence=sequence, payload=payload, payload_hash=_hash(payload)))

    def _incident(self, severity: str, code: str, message: str, blocking: bool) -> None:
        safe_message = message if len(message) <= 500 else f"{message[:497]}..."
        self.store.append_incident(OperationalIncident(incident_id=_id("ibkrincident", [severity, code, safe_message, utcnow()]), severity=severity, code=code, message=safe_message, blocking=blocking))


ibkr_acceptance_service = IbkrPaperAcceptanceService()

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.trading.models import InstrumentReference, MarketReference, OrderIntent
from backend.trading.services import TradingControlService
from backend.brokers import broker_registry
from backend.brokers.errors import BrokerError
from backend.brokers.models import BrokerCancelCommand, BrokerOrderCommand
from backend.trading.models import OrderStatus, PaperFill, LedgerEntry

router = APIRouter(prefix="/api/trading/paper", tags=["trading-paper"])
service = TradingControlService()


class AccountCreateRequest(BaseModel):
    name: str = "SYNTHETIC PAPER SCALE TEST"
    initial_cash: Decimal = Field(default_factory=lambda: Decimal(os.getenv("PAPER_ACCOUNT_STARTING_BALANCE", "1000000")), gt=0)
    base_currency: str = Field(default_factory=lambda: os.getenv("PAPER_ACCOUNT_CURRENCY", "USD").upper())


class DeploymentCreateRequest(BaseModel):
    account_id: str
    candidate_id: str
    strategy_id: str
    strategy_version: str
    instrument: InstrumentReference
    selected_parameters: dict[str, Any] = Field(default_factory=dict)


class DeploymentApprovalRequest(BaseModel):
    approver: str
    notes: str = ""
    strategy_hash: str
    candidate_hash: str


class IntentSubmitRequest(BaseModel):
    intent: OrderIntent
    market: MarketReference


class SimulateRequest(BaseModel):
    market: MarketReference


class EmergencyRequest(BaseModel):
    enabled: bool
    reason: str
    updated_by: str = "system"


class BrokerSubmitRequest(BaseModel):
    broker: str = "ibkr"
    broker_account_id: str | None = None
    user_confirmed: bool = True


@router.post("/accounts")
def create_account(payload: AccountCreateRequest) -> dict[str, Any]:
    return service.create_account(payload.name, payload.initial_cash, payload.base_currency).model_dump(mode="json")


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in service.list_accounts()]}


@router.get("/accounts/{account_id}/snapshot")
def account_snapshot(account_id: str) -> dict[str, Any]:
    try:
        return service.snapshot(account_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/emergency-disable")
def emergency_disable(account_id: str, payload: EmergencyRequest) -> dict[str, Any]:
    return service.set_emergency_disable(account_id, payload.enabled, payload.reason, payload.updated_by).model_dump(mode="json")


@router.post("/accounts/{account_id}/reconcile")
def reconcile(account_id: str) -> dict[str, Any]:
    try:
        return service.reconcile(account_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deployments")
def create_deployment(payload: DeploymentCreateRequest) -> dict[str, Any]:
    try:
        return service.create_deployment(
            account_id=payload.account_id,
            candidate_id=payload.candidate_id,
            strategy_id=payload.strategy_id,
            strategy_version=payload.strategy_version,
            instrument=payload.instrument,
            selected_parameters=payload.selected_parameters,
        ).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/deployments")
def list_deployments() -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in service.list_deployments()]}


@router.post("/deployments/{deployment_id}/approve")
def approve(deployment_id: str, payload: DeploymentApprovalRequest) -> dict[str, Any]:
    try:
        return service.approve_deployment(deployment_id, payload.approver, payload.notes, payload.strategy_hash, payload.candidate_hash).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deployments/{deployment_id}/stale")
def stale(deployment_id: str, reasons: list[str]) -> dict[str, Any]:
    try:
        return service.mark_deployment_stale(deployment_id, reasons).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/intents")
def submit_intent(payload: IntentSubmitRequest) -> dict[str, Any]:
    try:
        result = service.submit_intent(payload.intent, payload.market)
        return {key: value.model_dump(mode="json") if value is not None else None for key, value in result.items()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/orders")
def list_orders(account_id: str | None = None) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in service.list_orders(account_id)]}


@router.post("/orders/{order_id}/simulate")
def simulate(order_id: str, payload: SimulateRequest) -> dict[str, Any]:
    try:
        result = service.simulate_order(order_id, payload.market)
        return {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value for key, value in result.items()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, reason: str = "user requested") -> dict[str, Any]:
    try:
        return service.cancel_order(order_id, reason).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/orders/{order_id}/submit-to-broker")
async def submit_to_broker(order_id: str, payload: BrokerSubmitRequest) -> dict[str, Any]:
    try:
        order = service.get_order(order_id)
        if not payload.user_confirmed:
            raise HTTPException(status_code=403, detail={"code": "USER_APPROVAL_REQUIRED", "message": "user approval required"})
        if order.status != OrderStatus.APPROVED:
            raise HTTPException(status_code=409, detail={"code": "ORDER_NOT_APPROVED", "message": "canonical order must be approved"})
        adapter = broker_registry.get(payload.broker)
        await adapter.connect()
        broker_account_id = payload.broker_account_id or order.account_id
        command = BrokerOrderCommand(
            canonical_order_id=order.order_id,
            account_id=broker_account_id,
            instrument_id=order.instrument_id,
            side=order.side.value,
            order_type=order.order_type.value,
            time_in_force=order.time_in_force.value,
            quantity=order.quantity,
            approved_quantity=order.quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            risk_evaluation_id=order.risk_evaluation_id,
            idempotency_key=order.idempotency_key,
            correlation_id=order.correlation_id,
            user_approval=payload.user_confirmed,
        )
        receipt = await adapter.submit_order(command)
        service.oms.transition(order, OrderStatus.SUBMITTED_TO_SIMULATOR, "submitted to broker adapter")
        service.oms.transition(order, OrderStatus.ACKNOWLEDGED, "broker acknowledged order")
        if receipt.execution:
            service.oms.apply_fill(order, receipt.execution.quantity, receipt.execution.price)
            fill = PaperFill(
                order_id=order.order_id,
                account_id=order.account_id,
                deployment_id=order.deployment_id,
                instrument_id=order.instrument_id,
                side=order.side,
                quantity=receipt.execution.quantity,
                price=receipt.execution.price,
                fees=receipt.execution.commission,
                execution_model_id="ibkr-paper",
                execution_model_version=1,
                liquidity_reason="broker_execution",
                metadata={"broker": payload.broker, "broker_order_id": receipt.order.broker_order_id, "permanent_id": receipt.order.permanent_id},
            )
            service.store.append_fill(fill)
            service.store.append_ledger([
                LedgerEntry(
                    account_id=order.account_id,
                    event_type="broker_fill",
                    instrument_id=order.instrument_id,
                    quantity_delta=receipt.execution.quantity if order.side.value == "BUY" else -receipt.execution.quantity,
                    price=receipt.execution.price,
                    fees=receipt.execution.commission,
                    causation_id=order.order_id,
                    metadata={"broker": payload.broker, "broker_order_id": receipt.order.broker_order_id},
                )
            ])
        order.metadata.update({"broker": payload.broker, "broker_order": receipt.order.model_dump(mode="json"), "submission_state": receipt.submission_state})
        order.metadata["broker_account_id"] = broker_account_id
        service.store.upsert_order(order)
        service._rebuild_and_store_account(order.account_id, marks={order.instrument_id: receipt.execution.price} if receipt.execution else None)
        return {"order": order.model_dump(mode="json"), "broker_receipt": receipt.model_dump(mode="json")}
    except BrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/orders/{order_id}/cancel-at-broker")
async def cancel_at_broker(order_id: str, reason: str = "user requested") -> dict[str, Any]:
    try:
        order = service.get_order(order_id)
        broker_order = order.metadata.get("broker_order") or {}
        broker_order_id = broker_order.get("broker_order_id")
        if not broker_order_id:
            raise HTTPException(status_code=409, detail={"code": "BROKER_ORDER_NOT_LINKED", "message": "order has no linked broker order"})
        adapter = broker_registry.get(str(order.metadata.get("broker") or "ibkr"))
        receipt = await adapter.cancel_order(BrokerCancelCommand(canonical_order_id=order.order_id, broker_order_id=str(broker_order_id), account_id=order.account_id, reason=reason))
        cancelled = service.cancel_order(order_id, reason, requested_by="broker")
        return {"order": cancelled.model_dump(mode="json"), "broker_receipt": receipt.model_dump(mode="json")}
    except BrokerError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/orders/{order_id}/broker-status")
def broker_status(order_id: str) -> dict[str, Any]:
    try:
        order = service.get_order(order_id)
        return {"order_id": order.order_id, "broker": order.metadata.get("broker"), "broker_order": order.metadata.get("broker_order"), "submission_state": order.metadata.get("submission_state")}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

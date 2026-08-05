from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.deps import get_current_user
from backend.forex_strategies.models import ExecutionMode, StrategyStatus
from backend.forex_strategies.registry import strategy_registry
from backend.forex_strategies.ibkr_acceptance import ibkr_acceptance_service
from backend.forex_strategies.service import forex_signal_service

router = APIRouter(tags=["forex-strategies"])


class GenerateRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "1h"
    price: float = 1.085
    regime: str = "trend"
    session: str = "london"
    data_quality: float = 1
    stale: bool = False
    spread: float = 0.00008
    framework_agreement: float = 0.66
    framework_bias: str = "BULLISH"
    frameworks: list[str] = Field(default_factory=lambda: ["trend_following", "price_action", "support_resistance", "breakout", "momentum", "smc", "ict"])
    framework_signal_ids: list[str] = Field(default_factory=list)
    feature_vector_id: str | None = None
    source_dataset_id: str | None = None


class ActionRequest(BaseModel):
    account_id: str | None = None
    reason: str = "user requested"
    execution_provider: str = "LOCAL_SIMULATOR"


class ExecutionModeRequest(BaseModel):
    execution_mode: ExecutionMode


@router.get("/api/forex-strategies")
def list_strategies() -> dict[str, Any]:
    return {"items": [item.model_dump(mode="json") for item in strategy_registry.all()], "instrument_status": forex_signal_service.instrument_status()}


@router.get("/api/forex-strategies/{strategy_id}")
def get_strategy(strategy_id: str) -> dict[str, Any]:
    try:
        return strategy_registry.require(strategy_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc


@router.get("/api/forex-strategies/{strategy_id}/performance")
def strategy_performance(strategy_id: str) -> dict[str, Any]:
    strategy_registry.require(strategy_id)
    trades = [trade for trade in forex_signal_service.list_trades() if trade.strategy_id == strategy_id]
    return {"strategy_id": strategy_id, "trade_count": len(trades), "open_trade_count": len([trade for trade in trades if trade.status == "OPEN"]), "net_pnl": sum(trade.unrealized_pnl for trade in trades), "auto_parameter_changes": False}


@router.post("/api/forex-strategies/{strategy_id}/enable-paper")
def enable_paper(strategy_id: str, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    strategy = strategy_registry.require(strategy_id)
    if strategy.validation_scorecard_id is None:
        raise HTTPException(status_code=409, detail={"code": "VALIDATION_LINEAGE_REQUIRED", "message": "paper approval requires validation lineage"})
    strategy.enabled = True
    strategy.status = StrategyStatus.PAPER_APPROVED
    strategy.execution_mode = ExecutionMode.MANUAL_CONFIRMATION
    return strategy.model_dump(mode="json")


@router.post("/api/forex-strategies/{strategy_id}/pause")
def pause(strategy_id: str, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return strategy_registry.pause(strategy_id).model_dump(mode="json")


@router.post("/api/forex-strategies/{strategy_id}/resume")
def resume(strategy_id: str, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return strategy_registry.resume(strategy_id).model_dump(mode="json")


@router.patch("/api/forex-strategies/{strategy_id}/execution-mode")
def execution_mode(strategy_id: str, payload: ExecutionModeRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if payload.execution_mode.value not in {"MANUAL_CONFIRMATION", "RULE_BASED_AUTO_PAPER", "DISABLED"}:
        raise HTTPException(status_code=400, detail="unsupported execution mode")
    try:
        return strategy_registry.set_execution_mode(strategy_id, payload.execution_mode).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/forex-signals/summary")
def signal_summary() -> dict[str, Any]:
    return forex_signal_service.summary()


@router.get("/api/forex-signals/candidates")
def list_candidates() -> dict[str, Any]:
    return {"items": [item.model_dump(mode="json") for item in forex_signal_service.list_candidates()]}


@router.post("/api/forex-signals/generate")
def generate_candidate(payload: GenerateRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return forex_signal_service.generate(payload.model_dump()).model_dump(mode="json")


@router.get("/api/forex-signals/candidates/{candidate_id}")
def get_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        return forex_signal_service.get_candidate(candidate_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="candidate not found") from exc


@router.post("/api/forex-signals/candidates/{candidate_id}/approve")
def approve_candidate(candidate_id: str, payload: ActionRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    try:
        candidate = forex_signal_service.approve(candidate_id, payload.account_id, payload.execution_provider)
        if candidate.oms_order_id:
            if payload.execution_provider == "LOCAL_SIMULATOR":
                candidate = forex_signal_service.simulate_fill(candidate_id)
        return candidate.model_dump(mode="json")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/forex-signals/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: str, payload: ActionRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return forex_signal_service.reject(candidate_id, payload.reason).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="candidate not found") from exc


@router.post("/api/forex-signals/candidates/{candidate_id}/cancel")
def cancel_candidate(candidate_id: str, payload: ActionRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return forex_signal_service.cancel(candidate_id, payload.reason).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="candidate not found") from exc


@router.get("/api/forex-execution/active")
def active_trades() -> dict[str, Any]:
    return {"items": [trade.model_dump(mode="json") for trade in forex_signal_service.list_trades() if trade.status == "OPEN"]}


@router.get("/api/forex-execution/history")
def execution_history() -> dict[str, Any]:
    return {"items": [trade.model_dump(mode="json") for trade in forex_signal_service.list_trades()]}


@router.get("/api/forex-execution/orders")
def execution_orders() -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in ibkr_acceptance_service.orders()]}


@router.get("/api/forex-execution/orders/{order_id}")
def execution_order_detail(order_id: str) -> dict[str, Any]:
    for row in ibkr_acceptance_service.orders():
        if row.broker_order_record_id == order_id or row.client_order_id == order_id or row.broker_order_id == order_id:
            return {"order": row.model_dump(mode="json"), "events": [event.model_dump(mode="json") for event in ibkr_acceptance_service.events(row.broker_order_record_id)]}
    raise HTTPException(status_code=404, detail="order not found")


@router.get("/api/forex-execution/orders/{order_id}/events")
def execution_order_events(order_id: str) -> dict[str, Any]:
    matched = None
    for row in ibkr_acceptance_service.orders():
        if row.broker_order_record_id == order_id or row.client_order_id == order_id or row.broker_order_id == order_id:
            matched = row.broker_order_record_id
            break
    if matched is None:
        raise HTTPException(status_code=404, detail="order not found")
    return {"items": [event.model_dump(mode="json") for event in ibkr_acceptance_service.events(matched)]}


@router.get("/api/forex-execution/orders/{order_id}/executions")
def execution_order_executions(order_id: str) -> dict[str, Any]:
    matched = None
    for row in ibkr_acceptance_service.orders():
        if row.broker_order_record_id == order_id or row.client_order_id == order_id or row.broker_order_id == order_id:
            matched = row.broker_order_record_id
            break
    if matched is None:
        raise HTTPException(status_code=404, detail="order not found")
    return {"items": ibkr_acceptance_service.executions(matched)}


@router.post("/api/forex-execution/orders/{order_id}/cancel")
def execution_order_cancel(order_id: str, _payload: ActionRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    raise HTTPException(status_code=409, detail={"code": "BROKER_CANCEL_NOT_CONNECTED", "message": "FX-5 cancel requires verified IBKR order linkage; local mutation is not performed"})


@router.get("/api/forex-execution/reconciliation")
def execution_reconciliation() -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in ibkr_acceptance_service.reconciliations()]}


@router.get("/api/forex-execution/reconciliation/{trade_id}")
def execution_reconciliation_detail(trade_id: str) -> dict[str, Any]:
    rows = [row for row in ibkr_acceptance_service.reconciliations() if row.trade_id == trade_id]
    return {"items": [row.model_dump(mode="json") for row in rows]}


@router.post("/api/forex-execution/reconciliation/run")
def execution_reconciliation_run(_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return ibkr_acceptance_service.reconcile().model_dump(mode="json")


@router.get("/api/forex-execution/incidents")
def execution_incidents() -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in ibkr_acceptance_service.incidents()]}


@router.post("/api/forex-execution/recovery/start")
def execution_recovery_start(_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return ibkr_acceptance_service.recovery_start()


@router.post("/api/forex-execution/recovery/complete")
def execution_recovery_complete(_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return ibkr_acceptance_service.recovery_complete()


@router.get("/api/forex-execution/{trade_id}")
def execution_detail(trade_id: str) -> dict[str, Any]:
    for trade in forex_signal_service.list_trades():
        if trade.trade_id == trade_id:
            return trade.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="trade not found")


@router.post("/api/forex-execution/{trade_id}/exit")
def exit_trade(trade_id: str, _payload: ActionRequest, _user: Any = Depends(get_current_user)) -> dict[str, Any]:
    for trade in forex_signal_service.list_trades():
        if trade.trade_id == trade_id:
            trade.status = "CLOSED"
            forex_signal_service.store.upsert_trade(trade)
            return trade.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="trade not found")

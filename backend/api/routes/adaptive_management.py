from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.adaptive_management.service import adaptive_management_service
from backend.auth.deps import get_current_user
from backend.models.user import User

router = APIRouter(prefix="/api/adaptive-management", tags=["adaptive-management"])


class ImportSessionRequest(BaseModel):
    session_id: str | None = None
    days: int | None = None
    payload: dict[str, Any] | None = None


class ReplayRequest(BaseModel):
    trade: dict[str, Any]
    candles: list[dict[str, Any]] = Field(default_factory=list)
    policy_ids: list[str] | None = None


class ReconstructRequest(BaseModel):
    trade: dict[str, Any]
    candles: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class WalkForwardRequest(BaseModel):
    session_id: str | None = None
    train_fraction: float = 0.6


class ActivateDemoRequest(BaseModel):
    approved_by: str = "local_api"
    eligible_symbols: list[str] | None = None
    eligible_strategies: list[str] | None = None
    maximum_actions_per_hour: int = 6


class DeactivateRequest(BaseModel):
    reason: str = "manual_deactivate"


class AdoptPositionRequest(BaseModel):
    position_id: str
    approved_by: str = "local_api"
    reason: str = ""


class MarkContaminatedRequest(BaseModel):
    position_id: str
    reason: str


class CloseIncidentPositionRequest(BaseModel):
    position_id: str
    incident_id: str | None = None
    reason: str = "VALIDATION_INCIDENT_CLOSE"
    requested_by: str = "operator"


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.status()


@router.get("/sessions")
async def sessions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.sessions()}


@router.get("/trades")
async def trades(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.trades()}


@router.get("/theses")
async def theses(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.theses()}


@router.get("/policies")
async def policies(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.policies()}


@router.get("/experiments")
async def experiments(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.experiments()}


@router.get("/shadow-decisions")
async def shadow_decisions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.shadow_decisions()}


@router.get("/activation")
async def activation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.activation()


@router.get("/open-positions")
async def open_positions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.open_positions()


@router.get("/positions")
async def positions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.open_positions()


@router.get("/positions/{ticket}")
async def position_detail(ticket: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.position_detail(ticket)


@router.get("/positions/{ticket}/history")
async def position_history(ticket: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.position_history(ticket)


@router.get("/performance")
async def performance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.performance()


@router.get("/shadow-summary")
async def shadow_summary(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.shadow_summary()


@router.get("/decisions")
async def decisions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"shadow_decisions": adaptive_management_service.shadow_decisions(), "management_actions": adaptive_management_service.actions()}


@router.get("/actions")
async def actions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": adaptive_management_service.actions()}


@router.get("/reconciliation")
async def reconciliation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.reconciliation()


@router.get("/reports/{session_id}")
async def report(session_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.report(session_id)


@router.get("/audit")
async def audit(session_id: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.audit_report(session_id=session_id)


@router.post("/import-session")
async def import_session(payload: ImportSessionRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    if payload.payload is not None:
        data = dict(payload.payload)
        if payload.session_id:
            data["session_id"] = payload.session_id
        return adaptive_management_service.import_session(data)
    return await adaptive_management_service.import_mt5_session(days=payload.days or 7, session_id=payload.session_id)


@router.post("/reconstruct-trade-path")
async def reconstruct_trade_path(payload: ReconstructRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.reconstruct_trade_path(payload.trade, payload.candles, context=payload.context)


@router.post("/replay")
async def replay(payload: ReplayRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.replay(payload.trade, payload.candles, policy_ids=payload.policy_ids)


@router.post("/evaluate-policies")
async def evaluate_policies(session_id: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.evaluate_policies(session_id=session_id)


@router.post("/run-walk-forward")
async def run_walk_forward(payload: WalkForwardRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.run_walk_forward(session_id=payload.session_id, train_fraction=payload.train_fraction)


@router.post("/detect-drift")
async def detect_drift(symbol: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.detect_drift(symbol=symbol)


@router.post("/activate-demo")
async def activate_demo(payload: ActivateDemoRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.activate_demo(
        approved_by=payload.approved_by,
        eligible_symbols=payload.eligible_symbols,
        eligible_strategies=payload.eligible_strategies,
        maximum_actions_per_hour=payload.maximum_actions_per_hour,
    )


@router.post("/deactivate")
async def deactivate(payload: DeactivateRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.deactivate(reason=payload.reason)


@router.post("/reset-circuit-breaker")
async def reset_circuit_breaker(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.reset_circuit_breaker()


@router.post("/adopt-existing-position")
async def adopt_existing_position(payload: AdoptPositionRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.adoption(position_id=payload.position_id, approved_by=payload.approved_by, reason=payload.reason)


@router.post("/mark-contaminated")
async def mark_contaminated(payload: MarkContaminatedRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.mark_contaminated(position_id=payload.position_id, reason=payload.reason)


@router.post("/close-incident-position")
async def close_incident_position(payload: CloseIncidentPositionRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.close_incident_position(position_id=payload.position_id, incident_id=payload.incident_id, reason=payload.reason, requested_by=payload.requested_by)


@router.post("/evaluate-now")
async def evaluate_now(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.evaluate_now()


@router.post("/evaluate")
async def evaluate(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.evaluate_now()


@router.post("/run-shadow")
async def run_shadow(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await adaptive_management_service.run_shadow_cycle()

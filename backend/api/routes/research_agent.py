from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.ai_assistant.security import MAX_REQUEST_BYTES, SafeAPIError, bounded_json_bytes, rate_limiter
from backend.auth.deps import get_current_user
from backend.models.user import User
from backend.research_agent.analysis import compare, failure_analysis, robustness
from backend.research_agent.approvals import approve_plan, reject_plan
from backend.research_agent.models import PlanStatus
from backend.research_agent.recommendations import recommend
from backend.research_agent.scheduler import scheduler
from backend.research_agent.services import ResearchAgentService, get_research_agent_service


router = APIRouter(prefix="/research-agent", tags=["research-agent"])


@router.get("/status", response_model=Dict[str, Any])
async def status(current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return service.status(user_id=str(current_user.id))


@router.get("/policies", response_model=Dict[str, Any])
async def policies(current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.list_policies(user_id=str(current_user.id))}


@router.post("/policies", response_model=Dict[str, Any])
async def create_policy(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    _guard(request, payload, current_user, "policy.create")
    try:
        return service.create_policy(payload, user_id=str(current_user.id))
    except SafeAPIError as exc:
        raise _safe(exc) from exc


@router.get("/policies/{policy_id}", response_model=Dict[str, Any])
async def get_policy(policy_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        return service.get_policy(policy_id, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc


@router.post("/policies/{policy_id}/activate", response_model=Dict[str, Any])
async def activate_policy(policy_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        from backend.research_agent.policies import policy_service

        return policy_service.activate(policy_id, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc


@router.post("/policies/{policy_id}/revoke", response_model=Dict[str, Any])
async def revoke_policy(policy_id: str, current_user: User = Depends(get_current_user)):
    try:
        from backend.research_agent.policies import policy_service

        return policy_service.revoke(policy_id, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc


@router.get("/plans", response_model=Dict[str, Any])
async def plans(current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.list_plans(user_id=str(current_user.id))}


@router.post("/plans", response_model=Dict[str, Any])
async def create_plan(payload: Dict[str, Any], request: Request, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    _guard(request, payload, current_user, "plan.create")
    try:
        return service.create_plan(payload, user_id=str(current_user.id))
    except SafeAPIError as exc:
        raise _safe(exc) from exc


@router.get("/plans/{plan_id}", response_model=Dict[str, Any])
async def get_plan(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        return service.get_plan(plan_id, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc


@router.patch("/plans/{plan_id}", response_model=Dict[str, Any])
async def update_plan(plan_id: str, payload: Dict[str, Any], current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        return service.update_plan(plan_id, payload, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc
    except SafeAPIError as exc:
        raise _safe(exc) from exc


@router.delete("/plans/{plan_id}", response_model=Dict[str, Any])
async def delete_plan(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        return service.delete_plan(plan_id, user_id=str(current_user.id))
    except LookupError as exc:
        raise _not_found() from exc


@router.post("/plans/{plan_id}/approve", response_model=Dict[str, Any])
async def approve(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return approve_plan(service.get_plan(plan_id, user_id=str(current_user.id)), user_id=str(current_user.id))


@router.post("/plans/{plan_id}/reject", response_model=Dict[str, Any])
async def reject(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return reject_plan(service.get_plan(plan_id, user_id=str(current_user.id)), user_id=str(current_user.id))


@router.post("/plans/{plan_id}/start", response_model=Dict[str, Any])
async def start(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    try:
        return service.start_plan(plan_id, user_id=str(current_user.id))
    except SafeAPIError as exc:
        raise _safe(exc) from exc


@router.post("/plans/{plan_id}/pause", response_model=Dict[str, Any])
async def pause(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return scheduler.mark(service.get_plan(plan_id, user_id=str(current_user.id)), status=PlanStatus.PAUSED, user_id=str(current_user.id))


@router.post("/plans/{plan_id}/resume", response_model=Dict[str, Any])
async def resume(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return scheduler.mark(service.get_plan(plan_id, user_id=str(current_user.id)), status=PlanStatus.APPROVED, user_id=str(current_user.id))


@router.post("/plans/{plan_id}/cancel", response_model=Dict[str, Any])
async def cancel(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return scheduler.mark(service.get_plan(plan_id, user_id=str(current_user.id)), status=PlanStatus.CANCELLED, user_id=str(current_user.id))


@router.get("/plans/{plan_id}/tasks", response_model=Dict[str, Any])
async def tasks(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.get_plan(plan_id, user_id=str(current_user.id)).get("tasks", [])}


@router.get("/plans/{plan_id}/lineage", response_model=Dict[str, Any])
async def lineage(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    plan = service.get_plan(plan_id, user_id=str(current_user.id))
    return {"plan_id": plan_id, "nodes": [plan_id] + [task["task_id"] for task in plan.get("tasks", [])], "edges": plan.get("dependencies", {})}


@router.get("/plans/{plan_id}/budget", response_model=Dict[str, Any])
async def budget(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return service.get_plan(plan_id, user_id=str(current_user.id)).get("budget", {})


@router.get("/plans/{plan_id}/reports", response_model=Dict[str, Any])
async def reports(plan_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.reports(plan_id, user_id=str(current_user.id))}


@router.post("/hypotheses", response_model=Dict[str, Any])
async def hypotheses(payload: Dict[str, Any], current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.hypotheses(payload, user_id=str(current_user.id))}


@router.post("/compare", response_model=Dict[str, Any])
async def compare_endpoint(payload: Dict[str, Any], current_user: User = Depends(get_current_user)):
    return compare(payload)


@router.post("/analyze", response_model=Dict[str, Any])
async def analyze(payload: Dict[str, Any], current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    plan_id = str(payload.get("plan_id") or "")
    plan = service.get_plan(plan_id, user_id=str(current_user.id)) if plan_id else {"tasks": []}
    return {"robustness": robustness(plan), "failure_analysis": failure_analysis(plan)}


@router.post("/recommend", response_model=Dict[str, Any])
async def recommend_endpoint(payload: Dict[str, Any], current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    plan_id = str(payload.get("plan_id") or "")
    plan = service.get_plan(plan_id, user_id=str(current_user.id)) if plan_id else {"plan_id": "unscoped"}
    return {"items": recommend(plan)}


@router.get("/kill-switches", response_model=Dict[str, Any])
async def kill_switches(current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return {"items": service.kill_switches(user_id=str(current_user.id))}


@router.post("/kill-switches", response_model=Dict[str, Any])
async def create_kill_switch(payload: Dict[str, Any], current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return service.create_kill_switch(payload, user_id=str(current_user.id))


@router.delete("/kill-switches/{switch_id}", response_model=Dict[str, Any])
async def delete_kill_switch(switch_id: str, current_user: User = Depends(get_current_user), service: ResearchAgentService = Depends(get_research_agent_service)):
    return service.delete_kill_switch(switch_id, user_id=str(current_user.id))


def _guard(request: Request, payload: Dict[str, Any], current_user: User, scope: str) -> None:
    if bounded_json_bytes(payload) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail={"code": "PAYLOAD_TOO_LARGE", "message": "request too large"})
    client = request.client.host if request.client else "unknown"
    rate_limiter.check(f"research-agent:{scope}:{current_user.id}:{client}", capacity=60)


def _safe(exc: SafeAPIError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, "correlation_id": exc.correlation_id})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found", "correlation_id": f"corr_{uuid4().hex[:12]}"})

from __future__ import annotations

from backend.ai_assistant.security import SafeAPIError
from backend.research_agent.audit import audit
from backend.research_agent.models import ApprovalState, PlanStatus
from backend.research_agent.store import store


def approve_plan(plan: dict, *, user_id: str) -> dict:
    if plan.get("owner_user_id") != user_id:
        raise LookupError("plan not found")
    plan["approval_state"] = ApprovalState.APPROVED.value
    plan["status"] = PlanStatus.APPROVED.value
    store.put("plans", plan["plan_id"], plan)
    audit("plan.approve", actor=user_id, target=plan["plan_id"])
    return plan


def reject_plan(plan: dict, *, user_id: str) -> dict:
    if plan.get("owner_user_id") != user_id:
        raise LookupError("plan not found")
    plan["approval_state"] = ApprovalState.REJECTED.value
    plan["status"] = PlanStatus.CANCELLED.value
    store.put("plans", plan["plan_id"], plan)
    audit("plan.reject", actor=user_id, target=plan["plan_id"])
    return plan

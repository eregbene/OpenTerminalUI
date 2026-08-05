from __future__ import annotations

from decimal import Decimal

from backend.ai_assistant.security import SafeAPIError
from backend.research_agent.audit import audit
from backend.research_agent.models import PlanStatus
from backend.research_agent.store import store


class ResearchBudgetService:
    def check_plan(self, plan: dict, policy: dict, *, user_id: str) -> dict:
        max_jobs = int(policy.get("maximum_jobs_per_day") or 0)
        if max_jobs and 1 > max_jobs:
            audit("budget.reject", actor=user_id, target=plan.get("plan_id"), result="jobs_exceeded")
            raise SafeAPIError(429, "RESEARCH_BUDGET_EXCEEDED", "research budget exceeded")
        cost = Decimal(str((plan.get("budget") or {}).get("estimated_provider_cost") or "0"))
        if cost > Decimal(str(policy.get("maximum_provider_cost") or "0")):
            audit("budget.reject", actor=user_id, target=plan.get("plan_id"), result="cost_exceeded")
            raise SafeAPIError(429, "RESEARCH_BUDGET_EXCEEDED", "research budget exceeded")
        return {"compatible": True, "warnings": []}

    def pause_for_budget(self, plan: dict, *, user_id: str, limit: str) -> dict:
        plan["status"] = PlanStatus.PAUSED_BUDGET.value
        plan["budget_limit_reached"] = limit
        store.put("plans", plan["plan_id"], plan)
        audit("budget.pause", actor=user_id, target=plan["plan_id"], result=limit)
        return plan


budget_service = ResearchBudgetService()

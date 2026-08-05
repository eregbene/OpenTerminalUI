from __future__ import annotations

from backend.research_agent.audit import audit
from backend.research_agent.models import PlanStatus, now_iso
from backend.research_agent.store import store


class ResearchScheduler:
    def status(self) -> dict:
        plans = store.read().get("plans", {})
        return {
            "healthy": True,
            "queued": sum(1 for row in plans.values() if row.get("status") in {PlanStatus.APPROVED.value, PlanStatus.READY.value}),
            "running": sum(1 for row in plans.values() if row.get("status") == PlanStatus.RUNNING.value),
            "framework": "existing durable job framework compatible",
        }

    def mark(self, plan: dict, *, status: PlanStatus, user_id: str) -> dict:
        plan["status"] = status.value
        if status is PlanStatus.PAUSED:
            plan["paused_at"] = now_iso()
        plan["updated_at"] = now_iso()
        store.put("plans", plan["plan_id"], plan)
        audit(f"plan.{status.value.lower()}", actor=user_id, target=plan["plan_id"])
        return plan


scheduler = ResearchScheduler()

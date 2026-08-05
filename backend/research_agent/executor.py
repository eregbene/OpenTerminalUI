from __future__ import annotations

from backend.ai_assistant.security import SafeAPIError
from backend.research_agent.audit import audit
from backend.research_agent.models import PlanStatus, TaskStatus, now_iso
from backend.research_agent.store import store


FORBIDDEN_ACTION_PHRASES = (
    "create order",
    "submit order",
    "cancel order",
    "place order",
    "broker",
    "approve risk",
    "risk approval",
    "promote candidate",
    "activate deployment",
    "enable deployment",
    "paper order",
    "mutate position",
    "change balance",
)


def _contains_forbidden_request(plan: dict) -> bool:
    text = " ".join(str(plan.get(field, "")) for field in ("title", "objective")).lower()
    return any(phrase in text for phrase in FORBIDDEN_ACTION_PHRASES)


class ResearchExecutor:
    def execute(self, plan: dict, *, policy: dict, user_id: str) -> dict:
        if plan.get("approval_state") not in {"APPROVED", "NOT_REQUIRED"}:
            raise SafeAPIError(403, "APPROVAL_REQUIRED", "plan approval required")
        if _contains_forbidden_request(plan):
            raise SafeAPIError(403, "FORBIDDEN_RESEARCH_ACTION", "forbidden action")
        plan["status"] = PlanStatus.RUNNING.value
        plan["started_at"] = plan.get("started_at") or now_iso()
        for task in plan.get("tasks") or []:
            task["status"] = TaskStatus.COMPLETED.value
            artifact_id = f"artifact_{task['task_id']}"
            task["created_artifacts"].append(artifact_id)
            task["lineage"].append({"from": plan["plan_id"], "to": artifact_id, "relation": "created"})
        plan["status"] = PlanStatus.COMPLETED.value
        plan["completed_at"] = now_iso()
        store.put("plans", plan["plan_id"], plan)
        audit("plan.execute", actor=user_id, target=plan["plan_id"], payload={"task_count": len(plan.get("tasks") or [])})
        return plan


executor = ResearchExecutor()

from __future__ import annotations

from typing import Any

from backend.ai_assistant.security import SafeAPIError, validate_identifier
from backend.research_agent.approvals import approve_plan, reject_plan
from backend.research_agent.audit import audit
from backend.research_agent.budgets import budget_service
from backend.research_agent.executor import executor
from backend.research_agent.hypotheses import generate_hypotheses
from backend.research_agent.models import PlanStatus, ResearchAgentMode
from backend.research_agent.planner import planner
from backend.research_agent.policies import policy_service
from backend.research_agent.reports import create_report
from backend.research_agent.scheduler import scheduler
from backend.research_agent.store import store


class ResearchAgentService:
    permissions = {
        "research_agent.view",
        "research_agent.propose",
        "research_agent.create_plan",
        "research_agent.approve_plan",
        "research_agent.run",
        "research_agent.pause",
        "research_agent.cancel",
        "research_agent.manage_policy",
        "research_agent.manage_kill_switch",
        "research_agent.view_cost",
        "research_agent.view_audit",
        "research_agent.admin",
    }

    def status(self, *, user_id: str) -> dict:
        data = store.read()
        active_policy = policy_service.active(user_id=user_id)
        return {
            "mode": ResearchAgentMode.ASSISTED.value,
            "autonomous_default": False,
            "active_policy": active_policy,
            "kill_switches": list(data.get("kill_switches", {}).values()),
            "scheduler": scheduler.status(),
            "health": "healthy",
        }

    def list_policies(self, *, user_id: str) -> list[dict]:
        return policy_service.list(user_id=user_id)

    def create_policy(self, payload: dict, *, user_id: str) -> dict:
        return policy_service.create(payload, user_id=user_id)

    def get_policy(self, policy_id: str, *, user_id: str) -> dict:
        return policy_service.get(policy_id, user_id=user_id)

    def create_plan(self, payload: dict, *, user_id: str) -> dict:
        policy = policy_service.active(user_id=user_id, workspace_id=str(payload.get("workspace_id") or "default"))
        plan = planner.create_plan(payload, user_id=user_id, policy=policy)
        if policy:
            budget_service.check_plan(plan, policy, user_id=user_id)
        store.put("plans", plan["plan_id"], plan)
        audit("plan.create", actor=user_id, target=plan["plan_id"], payload={"policy_id": plan.get("policy_id")})
        return plan

    def list_plans(self, *, user_id: str) -> list[dict]:
        return [row for row in store.read().get("plans", {}).values() if row.get("owner_user_id") == user_id]

    def get_plan(self, plan_id: str, *, user_id: str) -> dict:
        row = store.read().get("plans", {}).get(validate_identifier(plan_id))
        if not row or row.get("owner_user_id") != user_id:
            raise LookupError("plan not found")
        return row

    def update_plan(self, plan_id: str, payload: dict, *, user_id: str) -> dict:
        plan = self.get_plan(plan_id, user_id=user_id)
        if plan.get("status") in {PlanStatus.RUNNING.value, PlanStatus.COMPLETED.value}:
            raise SafeAPIError(409, "PLAN_IMMUTABLE", "plan cannot be changed")
        plan.update({key: value for key, value in payload.items() if key in {"title", "objective", "schedule"}})
        store.put("plans", plan_id, plan)
        audit("plan.update", actor=user_id, target=plan_id)
        return plan

    def delete_plan(self, plan_id: str, *, user_id: str) -> dict:
        data = store.read()
        plan = self.get_plan(plan_id, user_id=user_id)
        data.get("plans", {}).pop(plan["plan_id"], None)
        store.write(data)
        audit("plan.delete", actor=user_id, target=plan_id)
        return {"deleted": True, "plan_id": plan_id}

    def start_plan(self, plan_id: str, *, user_id: str) -> dict:
        plan = self.get_plan(plan_id, user_id=user_id)
        policy = policy_service.get(plan["policy_id"], user_id=user_id) if plan.get("policy_id") else policy_service.active(user_id=user_id)
        if not policy:
            raise SafeAPIError(403, "POLICY_REQUIRED", "active policy required")
        if self._blocked(plan, policy):
            raise SafeAPIError(403, "KILL_SWITCH_ACTIVE", "kill switch active")
        budget_service.check_plan(plan, policy, user_id=user_id)
        completed = executor.execute(plan, policy=policy, user_id=user_id)
        create_report(completed, user_id=user_id)
        return completed

    def hypotheses(self, payload: dict, *, user_id: str) -> list[dict]:
        rows = generate_hypotheses(payload, evidence_bundle_ids=list(payload.get("evidence_bundle_ids") or []))
        audit("hypothesis.generate", actor=user_id, payload={"count": len(rows)})
        return rows

    def reports(self, plan_id: str, *, user_id: str) -> list[dict]:
        plan = self.get_plan(plan_id, user_id=user_id)
        return [row for row in store.read().get("reports", {}).values() if row.get("plan_id") == plan["plan_id"]]

    def kill_switches(self, *, user_id: str) -> list[dict]:
        return list(store.read().get("kill_switches", {}).values())

    def create_kill_switch(self, payload: dict, *, user_id: str) -> dict:
        switch = {"switch_id": f"ks_{len(store.read().get('kill_switches', {})) + 1}", "scope": payload.get("scope") or "all", "target": payload.get("target") or "*", "active": True, "owner_user_id": user_id}
        store.put("kill_switches", switch["switch_id"], switch)
        audit("kill_switch.create", actor=user_id, target=switch["switch_id"])
        return switch

    def delete_kill_switch(self, switch_id: str, *, user_id: str) -> dict:
        data = store.read()
        data.get("kill_switches", {}).pop(validate_identifier(switch_id), None)
        store.write(data)
        audit("kill_switch.delete", actor=user_id, target=switch_id)
        return {"deleted": True, "switch_id": switch_id}

    def _blocked(self, plan: dict, policy: dict) -> bool:
        switches = store.read().get("kill_switches", {}).values()
        return any(row.get("active") and row.get("scope") in {"all", "research_plan"} and row.get("target") in {"*", plan.get("plan_id")} for row in switches)


_service: ResearchAgentService | None = None


def get_research_agent_service() -> ResearchAgentService:
    global _service
    if _service is None:
        _service = ResearchAgentService()
    return _service

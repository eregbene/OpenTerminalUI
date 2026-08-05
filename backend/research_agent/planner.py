from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.ai_assistant.security import SafeAPIError, validate_identifier
from backend.research_agent.models import ApprovalState, PlanStatus, ResearchAgentMode, TaskStatus, TaskType, now_iso


REGISTERED_TASKS = {task.value for task in TaskType}


class ResearchPlanner:
    def create_plan(self, payload: dict[str, Any], *, user_id: str, policy: dict[str, Any] | None) -> dict[str, Any]:
        workspace_id = validate_identifier(str(payload.get("workspace_id") or "default"))
        objective = str(payload.get("objective") or payload.get("title") or "").strip()
        if not objective:
            raise SafeAPIError(422, "INVALID_PLAN", "objective required")
        mode = ResearchAgentMode(str(payload.get("agent_mode") or ResearchAgentMode.ASSISTED.value))
        if mode is ResearchAgentMode.AUTONOMOUS_RESEARCH and not policy:
            raise SafeAPIError(403, "POLICY_REQUIRED", "active policy required")
        template = str(payload.get("template") or "baseline_validation")
        if policy and template not in set(policy.get("allowed_research_templates") or []):
            raise SafeAPIError(403, "POLICY_VIOLATION", "template not allowed")
        tasks = self._tasks(payload, policy=policy)
        graph = validate_graph(tasks)
        return {
            "plan_id": f"plan_{uuid4().hex[:12]}",
            "owner_user_id": validate_identifier(user_id),
            "workspace_id": workspace_id,
            "title": str(payload.get("title") or objective[:80]),
            "objective": objective,
            "status": PlanStatus.WAITING_APPROVAL.value if (policy or {}).get("approval_required", True) else PlanStatus.READY.value,
            "agent_mode": mode.value,
            "policy_id": (policy or {}).get("policy_id"),
            "asset_scope": payload.get("asset_scope") or ["EQUITY"],
            "instrument_scope": payload.get("instrument_scope") or [payload.get("instrument") or "TEST"],
            "strategy_scope": payload.get("strategy_scope") or ["ema_trend_continuation_v1"],
            "dataset_scope": payload.get("dataset_scope") or ["internal-demo"],
            "hypotheses": payload.get("hypotheses") or [],
            "tasks": graph,
            "dependencies": {task["task_id"]: task["dependencies"] for task in graph},
            "budget": {"estimated_provider_cost": "0.05", "estimated_provider_tokens": 500, "estimated_runtime_seconds": 30},
            "schedule": payload.get("schedule") or {"type": "one_time"},
            "approval_state": ApprovalState.PENDING.value if (policy or {}).get("approval_required", True) else ApprovalState.NOT_REQUIRED.value,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "started_at": None,
            "completed_at": None,
            "paused_at": None,
            "version": 1,
            "assumptions": ["Plan uses approved deterministic research services only.", "Candidate promotion and deployment remain human workflows."],
            "policy_decision": "compatible",
        }

    def _tasks(self, payload: dict[str, Any], *, policy: dict[str, Any] | None) -> list[dict[str, Any]]:
        base = [
            (TaskType.EVIDENCE_COLLECTION, []),
            (TaskType.DATASET_VALIDATION, ["task_01_evidence_collection"]),
            (TaskType.BASELINE_BACKTEST, ["task_02_dataset_validation"]),
            (TaskType.WALK_FORWARD_VALIDATION, ["task_03_baseline_backtest"]),
            (TaskType.ROBUSTNESS_REVIEW, ["task_04_walk_forward_validation"]),
            (TaskType.RESEARCH_REPORT_GENERATION, ["task_05_robustness_review"]),
        ]
        return [
            {
                "task_id": f"task_{index:02d}_{task.value}",
                "type": task.value,
                "inputs": {"objective": payload.get("objective"), "template": payload.get("template") or "baseline_validation"},
                "dependencies": deps,
                "expected_outputs": [task.value],
                "resource_estimate": {"provider_tokens": 50, "runtime_seconds": 5},
                "policy_decision": "compatible",
                "approval_state": ApprovalState.NOT_REQUIRED.value,
                "status": TaskStatus.PENDING.value,
                "retries": 0,
                "created_artifacts": [],
                "lineage": [],
            }
            for index, (task, deps) in enumerate(base, start=1)
        ]


def validate_graph(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {task["task_id"] for task in tasks}
    if len(tasks) > 25:
        raise SafeAPIError(422, "INVALID_GRAPH", "task graph too large")
    for task in tasks:
        if task.get("type") not in REGISTERED_TASKS:
            raise SafeAPIError(422, "INVALID_GRAPH", "unsupported task type")
        for dep in task.get("dependencies") or []:
            if dep not in ids:
                raise SafeAPIError(422, "INVALID_GRAPH", "missing dependency")
    _detect_cycles(tasks)
    return tasks


def _detect_cycles(tasks: list[dict[str, Any]]) -> None:
    deps = {task["task_id"]: set(task.get("dependencies") or []) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise SafeAPIError(422, "INVALID_GRAPH", "cycle detected")
        if node in visited:
            return
        visiting.add(node)
        for dep in deps.get(node, set()):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in deps:
        visit(node)


planner = ResearchPlanner()

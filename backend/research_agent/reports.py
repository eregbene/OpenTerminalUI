from __future__ import annotations

from uuid import uuid4

from backend.research_agent.analysis import failure_analysis, robustness
from backend.research_agent.recommendations import recommend
from backend.research_agent.store import store


def create_report(plan: dict, *, user_id: str, report_type: str = "completed_experiment") -> dict:
    report = {
        "report_id": f"rar_{uuid4().hex[:12]}",
        "owner_user_id": user_id,
        "plan_id": plan["plan_id"],
        "type": report_type,
        "objective": plan.get("objective"),
        "scope": {"instruments": plan.get("instrument_scope"), "datasets": plan.get("dataset_scope"), "strategies": plan.get("strategy_scope")},
        "methods": [task.get("type") for task in plan.get("tasks", [])],
        "results": {"status": plan.get("status"), "tasks_completed": sum(1 for task in plan.get("tasks", []) if task.get("status") == "COMPLETED")},
        "evidence_citations": plan.get("evidence_bundle_ids", []),
        "limitations": ["File-backed phase implementation is not multi-node production storage."],
        "costs": plan.get("budget"),
        "robustness": robustness(plan),
        "failure_analysis": failure_analysis(plan),
        "next_recommended_action": recommend(plan),
        "approval_requirements": plan.get("approval_state"),
        "markdown": f"# {plan.get('title')}\n\nStatus: {plan.get('status')}\n",
        "csv": "field,value\nstatus,%s\n" % plan.get("status"),
    }
    return store.put("reports", report["report_id"], report)

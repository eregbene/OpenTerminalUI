from __future__ import annotations

from backend.research_agent.models import FailureClass


def compare(payload: dict) -> dict:
    items = payload.get("items") or []
    return {"comparable_fields": ["status", "score", "dataset"], "non_comparable_fields": [], "items": items, "missing_data": [], "freshness": "unknown", "quality": "partial"}


def robustness(plan: dict) -> dict:
    return {
        "flags": ["requires_human_review"] if not plan.get("completed_at") else [],
        "checks": {
            "in_sample_vs_out_of_sample": "available_after_validation",
            "walk_forward_consistency": "reviewed",
            "overfitting_indicators": "not_authoritative_without_scorecard",
        },
    }


def failure_analysis(plan: dict) -> dict:
    failed = [task for task in plan.get("tasks", []) if task.get("status") == "FAILED"]
    return {
        "classification": FailureClass.EXECUTION_FAILURE.value if failed else FailureClass.UNKNOWN.value,
        "confirmed_cause": "task failure" if failed else None,
        "likely_contributing_factor": None,
        "unknown_cause": not failed,
        "recommended_next_test": "Review deterministic task artifacts and rerun only if policy allows.",
    }

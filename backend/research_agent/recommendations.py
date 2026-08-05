from __future__ import annotations


def recommend(plan: dict) -> list[dict]:
    return [
        {
            "recommendation": "Request human review of the completed research report before any candidate workflow.",
            "rationale": "Research Agent is not authorized to promote candidates or activate deployments.",
            "supporting_evidence": [plan.get("plan_id")],
            "expected_cost": "0",
            "policy_compatible": True,
            "required_approval": "human_review",
            "uncertainty": "MEDIUM",
            "stop_condition": "Stop if validation or robustness gates fail.",
        }
    ]

from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.ai_assistant.security import sanitize_value
from backend.research_agent.models import HypothesisConfidence


def generate_hypotheses(payload: dict[str, Any], *, evidence_bundle_ids: list[str] | None = None) -> list[dict[str, Any]]:
    objective = str(payload.get("objective") or "Evaluate approved research objective")[:240]
    instrument = str(payload.get("instrument") or "TEST")[:80]
    bundle_ids = evidence_bundle_ids or list(payload.get("evidence_bundle_ids") or [])
    return [
        sanitize_value(
            {
                "hypothesis_id": f"hyp_{uuid4().hex[:12]}",
                "statement": f"{instrument} may merit additional validation for: {objective}",
                "domain": "research",
                "evidence_bundle_ids": bundle_ids,
                "cited_observations": ["Hypothesis is proposed for testing, not asserted as fact."],
                "testable_condition": "Run approved baseline validation and compare deterministic scorecard gates.",
                "target_metric": "scorecard.total_score",
                "expected_direction": "higher_than_baseline",
                "invalidation_criteria": "Validation gates fail or robustness review flags material weakness.",
                "required_dataset": str(payload.get("dataset") or "internal-demo"),
                "required_strategy_template": str(payload.get("template") or "baseline_validation"),
                "estimated_cost": "0.05",
                "confidence": HypothesisConfidence.UNASSESSED.value,
                "known_limitations": ["No hypothesis is treated as confirmed before deterministic research completes."],
            }
        )
    ]

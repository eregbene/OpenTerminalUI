from __future__ import annotations

from backend.trading.models import RiskEvaluation


def explain_risk(evaluation: RiskEvaluation) -> list[str]:
    return [f"{rule.rule_id}: {rule.status} - {rule.message}" for rule in evaluation.rules_evaluated]

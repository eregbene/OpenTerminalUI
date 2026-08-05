from __future__ import annotations

from backend.strategies.models import StrategyDecision, TradeProposal


def proposal_to_overlay(proposal: TradeProposal) -> dict[str, object]:
    return {
        "overlay_id": proposal.proposal_id,
        "type": "strategy_proposal",
        "direction": proposal.direction,
        "price": proposal.reference_price,
        "invalidation_price": proposal.invalidation_price,
        "targets": [target.model_dump(mode="json") for target in proposal.target_levels],
        "label": f"{str(proposal.direction).upper()} proposal",
        "status": proposal.status,
        "tooltip_evidence": [proposal.rationale],
    }


def decision_to_summary(decision: StrategyDecision) -> dict[str, object]:
    return {
        "decision_id": decision.decision_id,
        "strategy_id": decision.strategy_id,
        "decision_type": decision.decision_type,
        "direction": decision.direction,
        "as_of_timestamp": decision.as_of_timestamp.isoformat(),
        "explanation": decision.explanation,
        "quality_score": decision.quality_score,
        "proposal_id": decision.proposal.proposal_id if decision.proposal else None,
    }

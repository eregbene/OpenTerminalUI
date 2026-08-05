from __future__ import annotations

from backend.strategies.models import StrategyDecision, StrategyEvent, TradeProposal, stable_id


def decision_event(decision: StrategyDecision) -> StrategyEvent:
    event_id = stable_id("evt", "decision", decision.decision_id)
    return StrategyEvent(
        event_type="strategy.decision.created",
        event_id=event_id,
        strategy_id=decision.strategy_id,
        strategy_version=decision.strategy_version,
        instrument_id=decision.instrument_id,
        as_of_timestamp=decision.as_of_timestamp,
        dataset_snapshot_id=decision.dataset_snapshot_id,
        correlation_id=decision.decision_id,
        idempotency_key=event_id,
        evidence_reference=decision.decision_id,
        payload={"decision_type": decision.decision_type, "direction": decision.direction},
    )


def proposal_event(decision: StrategyDecision, proposal: TradeProposal) -> StrategyEvent:
    event_id = stable_id("evt", "proposal", proposal.proposal_id)
    return StrategyEvent(
        event_type="strategy.proposal.created",
        event_id=event_id,
        strategy_id=decision.strategy_id,
        strategy_version=decision.strategy_version,
        instrument_id=proposal.instrument_id,
        as_of_timestamp=proposal.proposal_time,
        dataset_snapshot_id=decision.dataset_snapshot_id,
        correlation_id=decision.decision_id,
        idempotency_key=event_id,
        evidence_reference=proposal.proposal_id,
        payload={"proposal_id": proposal.proposal_id, "direction": proposal.direction, "reference_price": proposal.reference_price},
    )

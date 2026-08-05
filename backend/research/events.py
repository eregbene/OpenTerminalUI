from __future__ import annotations

from backend.research.models import LineageRecord, ResearchEvent, stable_id


def research_event(event_type: str, lineage: LineageRecord, *, job_id: str | None = None, payload: dict | None = None) -> ResearchEvent:
    event_id = stable_id("evt", event_type, job_id or lineage.correlation_id, lineage.parameter_set_hash)
    return ResearchEvent(
        event_type=event_type,
        event_id=event_id,
        correlation_id=lineage.correlation_id,
        job_id=job_id,
        strategy_id=lineage.strategy_id,
        strategy_version=lineage.strategy_version,
        dataset_snapshot_id=lineage.dataset_snapshot_id,
        configuration_hashes={
            "strategy": lineage.strategy_hash,
            "dataset": lineage.dataset_hash,
            "parameters": lineage.parameter_set_hash,
            "execution_model": lineage.execution_model_version,
            "cost_model": lineage.cost_model_version,
        },
        idempotency_key=event_id,
        payload=payload or {},
    )

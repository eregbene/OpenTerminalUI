from __future__ import annotations

from datetime import timezone

from backend.market_structure.models import MarketStructureEvent, MarketStructureSnapshot, stable_id


def build_events(snapshot: MarketStructureSnapshot) -> list[MarketStructureEvent]:
    out: list[MarketStructureEvent] = []
    for swing in snapshot.swings:
        out.append(_event("market_structure.swing.confirmed", swing.id, snapshot, swing.model_dump(mode="json")))
    for brk in snapshot.breaks:
        event_type = {
            "bos": "market_structure.bos.confirmed",
            "choch": "market_structure.choch.confirmed",
            "mss": "market_structure.mss.confirmed",
        }.get(str(brk.break_kind), "market_structure.break.confirmed")
        out.append(_event(event_type, brk.id, snapshot, brk.model_dump(mode="json")))
    for sweep in snapshot.liquidity_sweeps:
        out.append(_event("smc.liquidity.swept", sweep.id, snapshot, sweep.model_dump(mode="json")))
    for zone in snapshot.imbalances:
        out.append(_event("smc.fvg.created", zone.id, snapshot, zone.model_dump(mode="json")))
        if zone.mitigation_time:
            out.append(_event("smc.fvg.mitigated", f"{zone.id}:mitigated", snapshot, zone.model_dump(mode="json")))
    for block in snapshot.order_blocks:
        out.append(_event("smc.order_block.confirmed", block.id, snapshot, block.model_dump(mode="json")))
        if block.invalidation_time:
            out.append(_event("smc.order_block.invalidated", f"{block.id}:invalidated", snapshot, block.model_dump(mode="json")))
    for rng in snapshot.dealing_ranges:
        out.append(_event("smc.dealing_range.updated", rng.id, snapshot, rng.model_dump(mode="json")))
    seen: set[str] = set()
    unique: list[MarketStructureEvent] = []
    for event in out:
        if event.idempotency_key in seen:
            continue
        seen.add(event.idempotency_key)
        unique.append(event)
    return unique


def _event(event_type: str, source_id: str, snapshot: MarketStructureSnapshot, payload: dict[str, object]) -> MarketStructureEvent:
    event_id = stable_id("evt", event_type, source_id, snapshot.configuration_hash)
    return MarketStructureEvent(
        event_type=event_type,
        event_id=event_id,
        correlation_id=snapshot.snapshot_id,
        idempotency_key=event_id,
        occurred_at=snapshot.analysis_timestamp.astimezone(timezone.utc),
        source_dataset_id=snapshot.source_dataset_id,
        configuration_hash=snapshot.configuration_hash,
        payload=payload,
    )

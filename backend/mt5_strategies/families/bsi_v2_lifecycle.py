"""BSI V2 lifecycle and entry freshness.

Lifecycle semantics are BENSIM_ENGINEERING: they make stateless scheduler scans
safe without changing mentor setup definitions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.mt5_strategies.families.bsi_v2_primitives import (
    BSIFreshnessDecision,
    BSIIdentitySeed,
    BSILifecycleState,
    BSILifecycleTransition,
    build_bsi_entry_opportunity_id,
    build_bsi_thesis_id,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BSIEntryOpportunityRecord:
    bsi_thesis_id: str
    bsi_entry_opportunity_id: str
    state: BSILifecycleState
    created_at: datetime
    updated_at: datetime
    reason: str
    armed_at: datetime | None = None
    available_at: datetime | None = None
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    expired_at: datetime | None = None
    last_evaluated_at: datetime | None = None
    bsi_version: str | None = None
    subtype: str | None = None
    symbol: str | None = None
    direction: str | None = None
    timeframe: str | None = None
    replay_run_id: str | None = None
    account_execution_ids: tuple[str, ...] = ()
    source_structural_ids: tuple[str, ...] = ()
    liquidity_id: str | None = None
    liquidity_event_id: str | None = None
    entry_array_id: str | None = None
    retest_level: Decimal | None = None


@dataclass
class BSILifecycleStore:
    opportunities: dict[str, BSIEntryOpportunityRecord] = field(default_factory=dict)
    transitions: list[BSILifecycleTransition] = field(default_factory=list)

    def _opportunity_key(self, opportunity_id: str) -> str:
        return opportunity_id

    def _persist(self) -> None:
        return None

    def state_for(self, opportunity_id: str) -> BSILifecycleState | None:
        row = self.opportunities.get(self._opportunity_key(opportunity_id))
        return row.state if row else None

    def transition(
        self,
        *,
        thesis_id: str,
        opportunity_id: str,
        to_state: BSILifecycleState,
        reason: str,
        at: datetime | None = None,
    ) -> BSIEntryOpportunityRecord:
        occurred_at = at or _utcnow()
        key = self._opportunity_key(opportunity_id)
        current = self.opportunities.get(key)
        from_state = current.state if current else None
        if current is None:
            current = BSIEntryOpportunityRecord(
                bsi_thesis_id=thesis_id,
                bsi_entry_opportunity_id=opportunity_id,
                state=to_state,
                created_at=occurred_at,
                updated_at=occurred_at,
                reason=reason,
            )
            self.opportunities[key] = current
        else:
            current.state = to_state
            current.updated_at = occurred_at
            current.reason = reason
        current.last_evaluated_at = occurred_at
        if to_state == BSILifecycleState.ENTRY_ARMED:
            current.armed_at = occurred_at
        elif to_state == BSILifecycleState.ENTRY_AVAILABLE:
            current.available_at = occurred_at
        elif to_state == BSILifecycleState.CONSUMED:
            current.consumed_at = occurred_at
        elif to_state == BSILifecycleState.INVALIDATED:
            current.invalidated_at = occurred_at
        elif to_state == BSILifecycleState.EXPIRED:
            current.expired_at = occurred_at
        self.transitions.append(
            BSILifecycleTransition(
                bsi_thesis_id=thesis_id,
                bsi_entry_opportunity_id=opportunity_id,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                occurred_at=occurred_at,
            )
        )
        self._persist()
        return current

    def ensure_detected(
        self,
        seed: BSIIdentitySeed,
        *,
        at: datetime | None = None,
        account_id: str | None = None,
        replay_run_id: str | None = None,
    ) -> tuple[str, str, BSILifecycleState | None]:
        thesis_id = build_bsi_thesis_id(seed)
        opportunity_id = build_bsi_entry_opportunity_id(seed)
        prior_state = self.state_for(opportunity_id)
        if prior_state is None:
            self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.DETECTED, reason="detected", at=at)
            self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.THESIS_CREATED, reason="thesis_created", at=at)
            self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.ENTRY_ARMED, reason="entry_armed", at=at)
        self.annotate(seed, opportunity_id, account_id=account_id, replay_run_id=replay_run_id)
        return thesis_id, opportunity_id, prior_state

    def annotate(
        self,
        seed: BSIIdentitySeed,
        opportunity_id: str,
        *,
        account_id: str | None = None,
        replay_run_id: str | None = None,
    ) -> None:
        row = self.opportunities.get(self._opportunity_key(opportunity_id))
        if row is None:
            return
        row.bsi_version = seed.version
        row.subtype = seed.subtype
        row.symbol = seed.symbol
        row.direction = seed.direction
        row.timeframe = seed.timeframe
        row.replay_run_id = replay_run_id or row.replay_run_id
        row.source_structural_ids = tuple(x for x in (seed.structure_event_id,) if x)
        row.liquidity_id = seed.liquidity_id
        row.liquidity_event_id = seed.liquidity_id
        row.entry_array_id = seed.entry_array_id
        row.retest_level = seed.retest_level
        if account_id:
            execution_id = build_bsi_account_execution_id(opportunity_id, account_id)
            row.account_execution_ids = tuple(dict.fromkeys((*row.account_execution_ids, execution_id)))
        self._persist()

    def mark_available(self, thesis_id: str, opportunity_id: str, *, reason: str, at: datetime | None = None) -> None:
        self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.ENTRY_AVAILABLE, reason=reason, at=at)

    def mark_consumed(self, thesis_id: str, opportunity_id: str, *, reason: str, at: datetime | None = None) -> None:
        self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.CONSUMED, reason=reason, at=at)

    def mark_expired(self, thesis_id: str, opportunity_id: str, *, reason: str, at: datetime | None = None) -> None:
        self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.EXPIRED, reason=reason, at=at)

    def mark_invalidated(self, thesis_id: str, opportunity_id: str, *, reason: str, at: datetime | None = None) -> None:
        self.transition(thesis_id=thesis_id, opportunity_id=opportunity_id, to_state=BSILifecycleState.INVALIDATED, reason=reason, at=at)


def build_bsi_account_execution_id(opportunity_id: str, account_id: str) -> str:
    return f"{opportunity_id}:account:{account_id}"


class BSIDurableLifecycleStore(BSILifecycleStore):
    """JSON-backed lifecycle store for research/replay durability.

    The persisted key is scoped by namespace and replay_run_id. Account ids are
    stored as execution identities on the record, not baked into the methodology
    opportunity id.
    """

    def __init__(self, path: str | Path, *, namespace: str = "bsi_v2", replay_run_id: str | None = None) -> None:
        super().__init__()
        self.path = Path(path)
        self.namespace = namespace
        self.replay_run_id = replay_run_id
        self._loading = True
        self._load()
        self._loading = False

    def _opportunity_key(self, opportunity_id: str) -> str:
        return "|".join(part for part in (self.namespace, self.replay_run_id or "live", opportunity_id) if part)

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.opportunities = {
            key: _record_from_json(value)
            for key, value in payload.get("opportunities", {}).items()
        }
        self.transitions = [_transition_from_json(value) for value in payload.get("transitions", [])]

    def _persist(self) -> None:
        if getattr(self, "_loading", False):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "bsi_v2_lifecycle_v1",
            "namespace": self.namespace,
            "replay_run_id": self.replay_run_id,
            "opportunities": {key: _record_to_json(value) for key, value in self.opportunities.items()},
            "transitions": [_transition_to_json(value) for value in self.transitions],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _dt_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_json(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _record_to_json(row: BSIEntryOpportunityRecord) -> dict[str, Any]:
    return {
        "bsi_thesis_id": row.bsi_thesis_id,
        "bsi_entry_opportunity_id": row.bsi_entry_opportunity_id,
        "state": row.state.value,
        "created_at": _dt_to_json(row.created_at),
        "updated_at": _dt_to_json(row.updated_at),
        "reason": row.reason,
        "armed_at": _dt_to_json(row.armed_at),
        "available_at": _dt_to_json(row.available_at),
        "consumed_at": _dt_to_json(row.consumed_at),
        "invalidated_at": _dt_to_json(row.invalidated_at),
        "expired_at": _dt_to_json(row.expired_at),
        "last_evaluated_at": _dt_to_json(row.last_evaluated_at),
        "bsi_version": row.bsi_version,
        "subtype": row.subtype,
        "symbol": row.symbol,
        "direction": row.direction,
        "timeframe": row.timeframe,
        "replay_run_id": row.replay_run_id,
        "account_execution_ids": list(row.account_execution_ids),
        "source_structural_ids": list(row.source_structural_ids),
        "liquidity_id": row.liquidity_id,
        "liquidity_event_id": row.liquidity_event_id,
        "entry_array_id": row.entry_array_id,
        "retest_level": str(row.retest_level) if row.retest_level is not None else None,
    }


def _record_from_json(data: dict[str, Any]) -> BSIEntryOpportunityRecord:
    return BSIEntryOpportunityRecord(
        bsi_thesis_id=data["bsi_thesis_id"],
        bsi_entry_opportunity_id=data["bsi_entry_opportunity_id"],
        state=BSILifecycleState(data["state"]),
        created_at=_dt_from_json(data["created_at"]) or _utcnow(),
        updated_at=_dt_from_json(data["updated_at"]) or _utcnow(),
        reason=data["reason"],
        armed_at=_dt_from_json(data.get("armed_at")),
        available_at=_dt_from_json(data.get("available_at")),
        consumed_at=_dt_from_json(data.get("consumed_at")),
        invalidated_at=_dt_from_json(data.get("invalidated_at")),
        expired_at=_dt_from_json(data.get("expired_at")),
        last_evaluated_at=_dt_from_json(data.get("last_evaluated_at")),
        bsi_version=data.get("bsi_version"),
        subtype=data.get("subtype"),
        symbol=data.get("symbol"),
        direction=data.get("direction"),
        timeframe=data.get("timeframe"),
        replay_run_id=data.get("replay_run_id"),
        account_execution_ids=tuple(data.get("account_execution_ids", ())),
        source_structural_ids=tuple(data.get("source_structural_ids", ())),
        liquidity_id=data.get("liquidity_id"),
        liquidity_event_id=data.get("liquidity_event_id"),
        entry_array_id=data.get("entry_array_id"),
        retest_level=Decimal(str(data["retest_level"])) if data.get("retest_level") is not None else None,
    )


def _transition_to_json(row: BSILifecycleTransition) -> dict[str, Any]:
    return {
        "bsi_thesis_id": row.bsi_thesis_id,
        "bsi_entry_opportunity_id": row.bsi_entry_opportunity_id,
        "from_state": row.from_state.value if row.from_state else None,
        "to_state": row.to_state.value,
        "reason": row.reason,
        "occurred_at": _dt_to_json(row.occurred_at),
        "source_rule_ids": list(row.source_rule_ids),
        "evidence_class": row.evidence_class.value,
    }


def _transition_from_json(data: dict[str, Any]) -> BSILifecycleTransition:
    from backend.mt5_strategies.families.bsi_v2_primitives import BSIEvidenceClass

    return BSILifecycleTransition(
        bsi_thesis_id=data["bsi_thesis_id"],
        bsi_entry_opportunity_id=data["bsi_entry_opportunity_id"],
        from_state=BSILifecycleState(data["from_state"]) if data.get("from_state") else None,
        to_state=BSILifecycleState(data["to_state"]),
        reason=data["reason"],
        occurred_at=_dt_from_json(data["occurred_at"]) or _utcnow(),
        source_rule_ids=tuple(data.get("source_rule_ids", ("BSI2-LIFE-001",))),
        evidence_class=BSIEvidenceClass(data.get("evidence_class", "BENSIM_ENGINEERING")),
    )


def fixed_rr_target(entry: Decimal, stop: Decimal, direction: str, rr: Decimal = Decimal("2")) -> Decimal:
    risk = abs(entry - stop)
    return entry + risk * rr if direction == "LONG" else entry - risk * rr


def reward_risk(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal | None:
    risk = abs(entry - stop)
    if risk == 0:
        return None
    return abs(target - entry) / risk


def _side_valid(direction: str, entry: Decimal, stop: Decimal, target: Decimal) -> bool:
    if direction == "LONG":
        return stop < entry < target
    return target < entry < stop


def _mentor_safe_stop(
    *,
    direction: str,
    entry: Decimal,
    raw_stop: Decimal,
    atr: Decimal | None = None,
    spread: Decimal | None = None,
    broker_min_stop_distance: Decimal | None = None,
    min_atr_mult: Decimal = Decimal("1.0"),
    spread_mult: Decimal = Decimal("3"),
) -> tuple[Decimal, str, Decimal | None]:
    floor: Decimal | None = None
    floor_source = "mentor_safe_sssl"
    if atr is not None and atr > 0:
        floor = atr * min_atr_mult
    if spread is not None and spread > 0:
        spread_floor = spread * spread_mult
        if floor is None or spread_floor > floor:
            floor = spread_floor
    if broker_min_stop_distance is not None and broker_min_stop_distance > 0:
        if floor is None or broker_min_stop_distance > floor:
            floor = broker_min_stop_distance
            floor_source = "broker_min_stop"
    if floor is None or abs(entry - raw_stop) >= floor:
        return raw_stop, "mentor", floor
    return (entry - floor if direction == "LONG" else entry + floor), floor_source, floor


def evaluate_bare_retest_freshness(
    *,
    direction: str,
    bid: Decimal,
    ask: Decimal,
    original_liquidity_level: Decimal,
    mentor_invalidation_level: Decimal,
    tolerance: Decimal,
    min_rr: Decimal = Decimal("2"),
    broker_min_stop_distance: Decimal | None = None,
    planned_target: Decimal | None = None,
    atr: Decimal | None = None,
    spread: Decimal | None = None,
    min_stop_atr_mult: Decimal = Decimal("1.0"),
) -> BSIFreshnessDecision:
    entry = ask if direction == "LONG" else bid
    distance = abs(entry - original_liquidity_level)
    if distance > tolerance:
        return BSIFreshnessDecision(
            status="WAIT",
            reason="PRICE_NOT_AT_ORIGINAL_RETEST_LEVEL",
            entry_price=None,
            target=None,
            reward_risk=None,
            is_executable=False,
            mentor_entry_valid=False,
            mentor_setup_valid=True,
            execution_viable=False,
            engineering_adjustments={"distance": str(distance), "tolerance": str(tolerance)},
        )

    raw_stop = mentor_invalidation_level
    final_stop, constraint_source, engineering_min_stop = _mentor_safe_stop(
        direction=direction,
        entry=entry,
        raw_stop=raw_stop,
        atr=atr,
        spread=spread,
        broker_min_stop_distance=broker_min_stop_distance,
        min_atr_mult=min_stop_atr_mult,
    )
    if direction == "LONG" and final_stop >= entry:
        return BSIFreshnessDecision("EXPIRE", "INVALID_STOP_GEOMETRY", entry, None, None, False, True, True, False)
    if direction == "SHORT" and final_stop <= entry:
        return BSIFreshnessDecision("EXPIRE", "INVALID_STOP_GEOMETRY", entry, None, None, False, True, True, False)

    target = planned_target if planned_target is not None else fixed_rr_target(entry, final_stop, direction, min_rr)
    if planned_target is not None and constraint_source != "mentor" and min_rr is not None:
        planned_rr = reward_risk(entry, final_stop, planned_target)
        if planned_rr is None or planned_rr < min_rr:
            target = fixed_rr_target(entry, final_stop, direction, min_rr)
    rr = reward_risk(entry, final_stop, target)
    if rr is None or rr < min_rr:
        return BSIFreshnessDecision(
            status="EXPIRE",
            reason="REQUIRED_RR_DESTROYED_BY_DRIFT",
            entry_price=entry,
            target=target,
            reward_risk=rr,
            is_executable=False,
            mentor_entry_valid=True,
            mentor_setup_valid=True,
            execution_viable=False,
            engineering_adjustments={"constraint_source": constraint_source, "final_stop": str(final_stop)},
        )
    return BSIFreshnessDecision(
        status="AVAILABLE",
        reason="FRESH_RETEST_EXECUTABLE",
        entry_price=entry,
        target=target,
        reward_risk=rr,
        is_executable=True,
        mentor_entry_valid=True,
        mentor_setup_valid=True,
        execution_viable=True,
        engineering_adjustments={
            "mentor_invalidation_level": str(mentor_invalidation_level),
            "raw_structural_stop": str(raw_stop),
            "engineering_min_stop": str(engineering_min_stop) if engineering_min_stop is not None else None,
            "broker_min_stop": str(broker_min_stop_distance) if broker_min_stop_distance is not None else None,
            "final_stop": str(final_stop),
            "constraint_source": constraint_source,
        },
    )


def evaluate_zone_entry_freshness(
    *,
    direction: str,
    bid: Decimal,
    ask: Decimal,
    zone_low: Decimal,
    zone_high: Decimal,
    mentor_invalidation_level: Decimal,
    target: Decimal,
    min_rr: Decimal | None = None,
    broker_min_stop_distance: Decimal | None = None,
    atr: Decimal | None = None,
    spread: Decimal | None = None,
    min_stop_atr_mult: Decimal = Decimal("1.0"),
) -> BSIFreshnessDecision:
    entry = ask if direction == "LONG" else bid
    if not (zone_low <= entry <= zone_high):
        return BSIFreshnessDecision(
            status="WAIT",
            reason="PRICE_NOT_IN_ENTRY_ZONE",
            entry_price=None,
            target=None,
            reward_risk=None,
            is_executable=False,
            mentor_entry_valid=False,
            mentor_setup_valid=True,
            execution_viable=False,
            engineering_adjustments={"zone_low": str(zone_low), "zone_high": str(zone_high), "entry_quote": str(entry)},
        )

    raw_stop = mentor_invalidation_level
    final_stop, constraint_source, engineering_min_stop = _mentor_safe_stop(
        direction=direction,
        entry=entry,
        raw_stop=raw_stop,
        atr=atr,
        spread=spread,
        broker_min_stop_distance=broker_min_stop_distance,
        min_atr_mult=min_stop_atr_mult,
    )
    if not _side_valid(direction, entry, final_stop, target):
        return BSIFreshnessDecision(
            status="EXPIRE",
            reason="INVALID_EXECUTION_GEOMETRY",
            entry_price=entry,
            target=target,
            reward_risk=None,
            is_executable=False,
            mentor_entry_valid=True,
            mentor_setup_valid=True,
            execution_viable=False,
            engineering_adjustments={"raw_structural_stop": str(raw_stop), "final_stop": str(final_stop), "constraint_source": constraint_source},
        )
    rr = reward_risk(entry, final_stop, target)
    if constraint_source != "mentor" and min_rr is not None and (rr is None or rr < min_rr):
        target = fixed_rr_target(entry, final_stop, direction, min_rr)
        rr = reward_risk(entry, final_stop, target)
    if min_rr is not None and (rr is None or rr < min_rr):
        return BSIFreshnessDecision(
            status="EXPIRE",
            reason="REQUIRED_RR_DESTROYED_BY_DRIFT",
            entry_price=entry,
            target=target,
            reward_risk=rr,
            is_executable=False,
            mentor_entry_valid=True,
            mentor_setup_valid=True,
            execution_viable=False,
            engineering_adjustments={"raw_structural_stop": str(raw_stop), "final_stop": str(final_stop), "constraint_source": constraint_source},
        )
    return BSIFreshnessDecision(
        status="AVAILABLE",
        reason="FRESH_ZONE_ENTRY_EXECUTABLE",
        entry_price=entry,
        target=target,
        reward_risk=rr,
        is_executable=True,
        mentor_entry_valid=True,
        mentor_setup_valid=True,
        execution_viable=True,
        engineering_adjustments={
            "mentor_invalidation_level": str(mentor_invalidation_level),
            "raw_structural_stop": str(raw_stop),
            "engineering_min_stop": str(engineering_min_stop) if engineering_min_stop is not None else None,
            "broker_min_stop": str(broker_min_stop_distance) if broker_min_stop_distance is not None else None,
            "final_stop": str(final_stop),
            "constraint_source": constraint_source,
        },
    )

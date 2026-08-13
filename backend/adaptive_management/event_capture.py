"""Adaptive Trade Manager Validation & Performance Analytics layer -- Parts 1 & 2.

Pure observation. Both capture functions here are called from
AdaptiveManagementService._monitor_cycle AFTER the real decision has already been made
(_select_action) and journaled (_persist_action) -- strictly downstream of, and with zero
influence over, _can_execute/_execute_action. The caller wraps every call here in its own
try/except (see service.py) so a persistence failure can never surface as a management-cycle
error or block a real protective/trailing action from executing.

capture_position_baseline() is idempotent (first-write-wins, like
MT5CandidateEvaluationORM.capture_cycle_candidate_evaluations) so it can safely be called every
cycle without ever overwriting the original entry/SL/TP snapshot.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptivePositionBaselineORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# Coarse grouping of the REAL action taxonomy already emitted by AdaptiveManagementService
# (service.py's _select_action/_persist_action) into the minimum categories this analytics
# layer's spec asked for. action_type (the real value) is always stored alongside this and is
# authoritative -- action_category is a derived convenience for cross-cutting reports only.
_ACTION_CATEGORY_MAP: dict[str, str] = {
    "HOLD": "NO_ACTION",
    "HOLD_WITH_GIVEBACK_RISK": "NO_ACTION",
    "MOVE_SL_BREAKEVEN": "MOVE_TO_BREAK_EVEN",
    "TRAIL_STOP": "TRAIL_SL",
    "MOVE_SL_TO_REDUCED_RISK": "TRAIL_SL",
    "EXTEND_TP": "MODIFY_TP",
    "REDUCE_TP": "MODIFY_TP",
    "PARTIAL_PROFIT": "PARTIAL_EXIT",
    "TP_PROGRESS_PARTIAL_PROTECT": "PARTIAL_EXIT",
    "TP_PROGRESS_PROFIT_LOCK": "PARTIAL_EXIT",
    "MFE_PROTECTION_CLOSE": "FULL_EXIT",
    "TIME_EXIT": "FULL_EXIT",
    "TP_PROGRESS_STRUCTURE_STOP": "FULL_EXIT",
    "THESIS_INVALIDATION_CLOSE": "THESIS_INVALIDATED",
    "EVENT_RISK_REDUCTION": "PORTFOLIO_PROTECTION_EXIT",
    "ECONOMIC_REDUCE_SIZE": "PORTFOLIO_PROTECTION_EXIT",
    "ECONOMIC_MANAGE_EXISTING_ONLY": "PORTFOLIO_PROTECTION_EXIT",
    "VALIDATION_INCIDENT_CLOSE": "EMERGENCY_EXIT",
}
_TRAILING_ACTION_TYPES = {"TRAIL_STOP", "MOVE_SL_TO_REDUCED_RISK"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def categorize_action(action_type: str) -> str:
    return _ACTION_CATEGORY_MAP.get(action_type, "OTHER")


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _is_at_or_beyond_breakeven(*, direction: str, entry_price: float | None, sl: float | None) -> bool:
    if entry_price is None or sl is None:
        return False
    if direction == "LONG":
        return sl >= entry_price
    if direction == "SHORT":
        return sl <= entry_price
    return False


def capture_management_event(
    *,
    cycle_run_id: str,
    state: Any,
    payload: dict[str, Any],
    action: Any,
    reason: str | None,
    market_regime: str | None = None,
) -> None:
    """Persist one observation row for the decision the manager just made this cycle for this
    position. Never raises past the caller's try/except; a capture failure must never be
    mistaken for a real management-cycle error."""
    sl_before = _float(state.current_sl)
    tp_before = _float(state.current_tp)
    sl_after = _float(action.requested_sl) if action.requested_sl is not None else sl_before
    tp_after = _float(action.requested_tp) if action.requested_tp is not None else tp_before
    entry_price = _float(state.entry_price)
    action_type = str(action.action_type or "HOLD")

    row = AdaptiveManagementEventORM(event_id="AME_" + _hash({"position_id": state.position_id, "action_id": action.action_id, "cycle_run_id": cycle_run_id})[:40])
    row.account_id = getattr(state, "account_id", None) or "demo_10k"
    row.cycle_run_id = cycle_run_id
    row.created_at = utcnow()
    row.position_id = str(state.position_id)
    row.symbol = str(state.symbol or "").upper()
    row.direction = str(state.direction or "").upper()
    row.entry_price = entry_price
    row.current_price = _float(payload.get("price_current") or payload.get("price_open"))
    row.original_sl = _float(state.original_sl)
    row.original_tp = _float(state.original_tp)
    row.sl_before = sl_before
    row.sl_after = sl_after
    row.tp_before = tp_before
    row.tp_after = tp_after
    unrealized_pnl = _float(payload.get("profit"))
    original_risk_money = _float(state.original_risk_money)
    # MONEY basis only (AdaptivePositionStateORM._compute_r's preferred path) -- current R is
    # not itself a persisted column on AdaptivePositionStateORM (only max/min achieved R are),
    # and reusing service.py's price-distance fallback here would require importing from
    # service.py, which imports this module -- so an honest None (not a re-derived estimate)
    # is used when original_risk_money isn't available yet.
    row.current_r = round(unrealized_pnl / original_risk_money, 4) if (unrealized_pnl is not None and original_risk_money) else None
    row.max_achieved_r = _float(state.max_achieved_r)
    row.min_achieved_r = _float(state.min_achieved_r)
    row.unrealized_pnl = unrealized_pnl
    row.action_type = action_type
    row.action_category = categorize_action(action_type)
    row.action_status = str(action.status or "")
    row.action_reason = reason
    row.manager_state = {
        "r_source": state.r_source,
        "winner_classification": state.winner_classification,
        "stop_quality_v2": state.stop_quality_v2,
        "partial_profit_stage": state.partial_profit_stage,
        "adopted": bool(state.adopted),
        "managed_automatically": bool(state.managed_automatically),
    }
    row.is_at_or_beyond_breakeven = _is_at_or_beyond_breakeven(direction=row.direction, entry_price=entry_price, sl=sl_after)
    row.is_trailing_action = action_type in _TRAILING_ACTION_TYPES
    row.strategy = state.strategy_id
    row.market_regime = market_regime or state.entry_regime
    row.atr = _float(state.entry_atr)
    row.structure_level = _float((action.evidence or {}).get("structure_level") or (action.evidence or {}).get("structure_stop"))
    row.raw_payload = {"evidence": action.evidence, "considered_actions": action.considered_actions}

    with SessionLocal() as db:
        db.add(row)
        db.commit()


def capture_position_baseline(*, state: Any) -> bool:
    """Write the immutable original-trade-state baseline exactly once. Idempotent -- a second
    call for a position that already has a baseline row is a no-op. Returns True if a new row
    was written."""
    with SessionLocal() as db:
        existing = db.get(AdaptivePositionBaselineORM, state.position_id)
        if existing is not None:
            return False

        original_entry = _float(state.original_entry) if state.original_entry is not None else _float(state.entry_price)
        original_sl = _float(state.original_sl)
        original_tp = _float(state.original_tp)
        initial_stop_distance = abs(original_entry - original_sl) if (original_entry is not None and original_sl is not None) else None
        initial_reward_risk = None
        if original_entry is not None and original_tp is not None and initial_stop_distance and initial_stop_distance > 0:
            initial_reward_risk = round(abs(original_tp - original_entry) / initial_stop_distance, 4)

        original_confidence, confidence_band, candidate_rank = _confidence_join(state.broker_ticket)

        row = AdaptivePositionBaselineORM(position_id=str(state.position_id))
        row.account_id = getattr(state, "account_id", None) or "demo_10k"
        row.broker_ticket = state.broker_ticket
        row.symbol = str(state.symbol or "").upper()
        row.direction = str(state.direction or "").upper()
        row.original_entry = original_entry
        row.original_sl = original_sl
        row.original_tp = original_tp
        row.initial_stop_distance = initial_stop_distance
        row.initial_reward_risk = initial_reward_risk
        row.initial_risk_money = _float(state.original_risk_money)
        row.original_strategy = state.strategy_id
        row.original_confidence = original_confidence
        row.confidence_band = confidence_band
        row.candidate_rank = candidate_rank
        db.add(row)
        db.commit()
    return True


def _confidence_join(broker_ticket: str | None) -> tuple[float | None, str | None, int | None]:
    """Best-effort join to the confidence-calibration layer's candidate evaluation record
    (Part 11). Returns (None, None, None) when no matching record exists -- e.g. a manually
    adopted position, or one opened before that layer existed. Never fabricates a value."""
    if not broker_ticket:
        return None, None, None
    try:
        from backend.brokers.mt5.orm import MT5CandidateEvaluationORM

        with SessionLocal() as db:
            row = db.query(MT5CandidateEvaluationORM).filter(MT5CandidateEvaluationORM.broker_ticket == str(broker_ticket)).first()
            if row is None:
                return None, None, None
            return row.overall_confidence, row.confidence_band, row.rank
    except Exception as exc:
        logger.warning("Adaptive baseline: confidence-evaluation join unavailable: %s", exc.__class__.__name__)
        return None, None, None

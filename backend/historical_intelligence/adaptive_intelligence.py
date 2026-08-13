"""Adaptive Manager DEMO_ACTIVE historical-intelligence observation (Part 17/18).

Answers, using ONLY real historical trade-evolution states (adaptive_statistics.state_
statistics, itself built from the Adaptive Trade Manager's OWN already-validated event/
counterfactual data -- see that module's docstring): "given this original setup + current trade
state, what tended to happen next for similar setups?" -- specifically the profit-protection
question (Part 18): once a trade reaches +0.5R/+0.75R/+1R, did similar historical trades tend to
continue, stall, reverse, hit TP, or round-trip?

Recommendation is ALWAYS one of the EXISTING action types (HOLD, MOVE_SL_TO_REDUCED_RISK,
MOVE_SL_BREAKEVEN, TRAIL_STOP, PARTIAL_PROFIT, MFE_PROTECTION_CLOSE, THESIS_INVALIDATION_CLOSE)
-- never invented (Part 15/17). Gated the same way as the entry side: HIST_INTEL requires
modes.demo_active_enabled() AND a reliable resolved sample (Part 16 -- reliability in
{USEFUL, STRONG}, never a raw win-rate cutoff).

SCOPE OF THIS PASS: this module computes and PERSISTS (via record_observation, hooked into
backend/adaptive_management/service.py::_monitor_cycle strictly AFTER _select_action/
_persist_action finalize the real decision) a full recommendation for observability -- but the
recommendation is NEVER applied to the real decision in this pass (recommendation_applied is
always False). Actually feeding this back into _select_action is a materially higher-risk change
to the live position-management pipeline than observing it, and -- as with the entry side -- the
current historical sample (freshly built this session) does not yet have enough resolved
trade-evolution states for ANY peer group to clear the reliability bar, so there is nothing yet
to safely validate a live-influencing change against. The mechanism is built, tested, and wired;
enabling live application is a deliberate follow-up once real evidence accumulates.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence import adaptive_statistics
from backend.historical_intelligence.adaptive_fingerprint import build_state_fingerprint
from backend.historical_intelligence.modes import demo_active_enabled
from backend.historical_intelligence.orm import AdaptiveIntelligenceObservationORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

_EXISTING_ACTIONS = {"HOLD", "MOVE_SL_TO_REDUCED_RISK", "MOVE_SL_BREAKEVEN", "TRAIL_STOP", "PARTIAL_PROFIT", "MFE_PROTECTION_CLOSE", "THESIS_INVALIDATION_CLOSE"}
_MIN_SAMPLE_FOR_RECOMMENDATION = 30
_RELIABLE_LEVELS = {"USEFUL", "STRONG"}


def _recommend_action(*, stats: dict[str, Any], current_r: float | None, is_at_or_beyond_breakeven: bool) -> str | None:
    """Transparent, multi-factor, never a single win-rate/threshold rule. Returns None (no
    recommendation, current engine logic authoritative) whenever the evidence isn't reliable
    enough -- see the sample/reliability gate in record_observation."""
    reversal_p = stats.get("probability_reversal") or 0.0
    round_trip_p = stats.get("probability_round_trip") or 0.0
    continuation_p = stats.get("probability_reach_plus_1r") or 0.0

    if current_r is not None and current_r >= 0.5 and (reversal_p >= 0.5 or round_trip_p >= 0.4):
        recommended = "MFE_PROTECTION_CLOSE" if round_trip_p >= 0.5 else "PARTIAL_PROFIT"
    elif current_r is not None and current_r >= 0.25 and not is_at_or_beyond_breakeven and reversal_p >= 0.4:
        recommended = "MOVE_SL_BREAKEVEN"
    elif continuation_p >= 0.6 and reversal_p < 0.3:
        recommended = "TRAIL_STOP"
    else:
        recommended = "HOLD"
    assert recommended in _EXISTING_ACTIONS  # defense-in-depth: never let a typo invent a new action type
    return recommended


def evaluate_adaptive_intelligence(
    *,
    strategy: str | None, symbol: str, direction: str, original_regime: str | None, current_regime: str | None,
    current_r: float | None, max_achieved_r: float | None, min_achieved_r: float | None,
    elapsed_seconds: float | None, is_at_or_beyond_breakeven: bool, is_trailing_action: bool,
    actual_action_type: str,
) -> dict[str, Any]:
    """Pure-ish (one Postgres aggregate read via adaptive_statistics) evaluation -- no broker
    calls, never mutates AdaptivePositionStateORM or any operational table."""
    try:
        if not demo_active_enabled():
            return {"status": "UNAVAILABLE", "reason": "HISTORICAL_INTELLIGENCE_MODE_NOT_DEMO_ACTIVE", "recommended_action_type": None}

        fields = build_state_fingerprint(
            strategy=strategy, symbol=symbol, direction=direction, original_regime=original_regime, current_regime=current_regime,
            current_r=current_r, max_achieved_r=max_achieved_r, min_achieved_r=min_achieved_r, elapsed_seconds=elapsed_seconds,
            is_at_or_beyond_breakeven=is_at_or_beyond_breakeven, is_trailing_action=is_trailing_action,
        )
        peer_group_hash = fields["peer_group_hash"]
        stats = adaptive_statistics.state_statistics(peer_group_hash)
        reliability = stats.get("reliability")
        sample_size = stats.get("resolved_sample_size") or 0

        result = {
            "status": "EVALUATED", "reason": None, "reliability": reliability, "resolved_sample_size": sample_size,
            "probability_reach_original_tp": stats.get("probability_reach_original_tp"),
            "probability_reach_plus_1r": stats.get("probability_reach_plus_1r"),
            "probability_reversal": stats.get("probability_reversal"),
            "probability_round_trip": stats.get("probability_round_trip"),
            "expected_additional_r": stats.get("expected_additional_r"),
            "peer_group_hash": peer_group_hash, "recommended_action_type": None,
        }
        if reliability not in _RELIABLE_LEVELS or sample_size < _MIN_SAMPLE_FOR_RECOMMENDATION:
            result.update({"status": "UNAVAILABLE", "reason": "STATE_SAMPLE_INSUFFICIENT"})
            return result

        result["recommended_action_type"] = _recommend_action(stats=stats, current_r=current_r, is_at_or_beyond_breakeven=is_at_or_beyond_breakeven)
        return result
    except Exception as exc:
        logger.warning("Adaptive historical intelligence evaluation failed for symbol=%s: %s", symbol, exc.__class__.__name__)
        return {"status": "UNAVAILABLE", "reason": f"HISTORICAL_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}", "recommended_action_type": None}


def _observation_id(position_id: str, cycle_run_id: str) -> str:
    return "AIO_" + hashlib.sha256(f"{position_id}:{cycle_run_id}".encode()).hexdigest()[:40]


def record_observation(*, cycle_run_id: str, state: Any, action: Any, market_regime: str | None) -> bool:
    """Called from backend/adaptive_management/service.py::_monitor_cycle, in the SAME best-
    effort, try/except-isolated style as event_capture.capture_management_event that it sits
    alongside -- STRICTLY AFTER _select_action/_persist_action have already finalized `action`,
    and AFTER capture_management_event itself (called immediately before this). Reads
    current_r/is_at_or_beyond_breakeven from the AdaptiveManagementEventORM row
    capture_management_event just wrote, rather than re-deriving them independently from
    `state` (AdaptivePositionStateORM does not reliably carry current_r as its own attribute --
    it is computed by event_capture.py from unrealized_pnl/original_risk_money at write time;
    reading the row it just produced is the single source of truth, not a second derivation
    that could silently drift from it). Never influences the real decision; recommendation_
    applied is always False (see this module's docstring)."""
    from backend.adaptive_management.orm import AdaptiveManagementEventORM

    observation_id = _observation_id(state.position_id, cycle_run_id)
    with SessionLocal() as db:
        if db.get(AdaptiveIntelligenceObservationORM, observation_id) is not None:
            return False

        # Matches event_capture.py's own event_id derivation exactly (sha256 of a sorted,
        # json.dumps-serialized payload, sliced to 40 hex chars).
        import json

        event_id = "AME_" + hashlib.sha256(json.dumps({"position_id": state.position_id, "action_id": action.action_id, "cycle_run_id": cycle_run_id}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:40]
        event_row = db.get(AdaptiveManagementEventORM, event_id)
        current_r = event_row.current_r if event_row is not None else None
        max_achieved_r = event_row.max_achieved_r if event_row is not None else getattr(state, "max_achieved_r", None)
        min_achieved_r = event_row.min_achieved_r if event_row is not None else getattr(state, "min_achieved_r", None)
        is_at_or_beyond_breakeven = bool(event_row.is_at_or_beyond_breakeven) if event_row is not None else False

        evaluation = evaluate_adaptive_intelligence(
            strategy=getattr(state, "strategy_id", None), symbol=state.symbol, direction=state.direction,
            original_regime=None, current_regime=market_regime, current_r=current_r,
            max_achieved_r=max_achieved_r, min_achieved_r=min_achieved_r,
            elapsed_seconds=None, is_at_or_beyond_breakeven=is_at_or_beyond_breakeven,
            is_trailing_action=False, actual_action_type=action.action_type,
        )
        row = AdaptiveIntelligenceObservationORM(
            observation_id=observation_id, position_id=state.position_id, cycle_run_id=cycle_run_id,
            strategy=getattr(state, "strategy_id", None), symbol=state.symbol,
            status=evaluation["status"], reason=evaluation.get("reason"), reliability=evaluation.get("reliability"),
            resolved_sample_size=evaluation.get("resolved_sample_size") or 0,
            probability_reach_original_tp=evaluation.get("probability_reach_original_tp"),
            probability_reach_plus_1r=evaluation.get("probability_reach_plus_1r"),
            probability_reversal=evaluation.get("probability_reversal"),
            probability_round_trip=evaluation.get("probability_round_trip"),
            expected_additional_r=evaluation.get("expected_additional_r"),
            actual_action_type=action.action_type, recommended_action_type=evaluation.get("recommended_action_type"),
            recommendation_applied=False, peer_group_hash=evaluation.get("peer_group_hash"),
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
    return True

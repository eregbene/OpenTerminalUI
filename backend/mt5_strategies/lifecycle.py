"""Strategy lifecycle governance (Priority 6, 2026-08-21).

A single authoritative model for "what evidentiary stage is this strategy at, and what
evidence got it there" -- richer than, and layered ON TOP OF, the existing 3-value
MT5_STRATEGY_ACTIVATION_<ID> mechanism (ACTIVE_MT5/SHADOW_MT5/DISABLED). This module never
writes an env var and never itself changes what executes live -- lifecycle_state is a
TRACKING/RECOMMENDATION concept; the real capital-risk switch stays the existing, separate,
human-reviewed docker-compose/.env deploy, exactly as it already was for every activation
change made in Priorities 3-4. Moving a strategy's lifecycle_state to ACTIVE here is a
statement that the evidence supports it -- not an action that risks a single dollar.

States (progression):
    RESEARCH -> BACKTESTED -> ROBUSTNESS_TESTED -> SHADOW -> DEMO -> LIVE_ELIGIBLE -> ACTIVE
States (degradation, reachable from SHADOW/DEMO/LIVE_ELIGIBLE/ACTIVE):
    DEGRADED -> SUSPENDED -> RETIRED

Integration, not duplication:
  - strategy_performance_monitor.py's own real/shadow expectancy + its DEMOTE/PROMOTE_
    EXPECTANCY_R_THRESHOLD constants are the primary evidence source and are REUSED here
    (not reimplemented) -- see gather_evidence().
  - The 2017-2026 diagnostic audit's already-computed per-strategy classification
    (docs/strategy-diagnostic-audit-2017-2026.md) is the historical-robustness evidence
    source -- reused as a static lookup (_AUDIT_CLASSIFICATIONS below), not recomputed. Running
    strategy_robustness.py's bootstrap/Monte Carlo machinery fresh per lifecycle evaluation
    would be exactly the kind of uncapped, CPU-contending-with-live-trading work this session
    has repeatedly avoided; the audit's own findings are re-usable evidence, not a one-time
    report to be forgotten.
  - confidence_calibration.py's per-strategy correlation is reused directly (already a cheap,
    fast, existing query -- see gather_evidence()).
  - trust_gating.get_trust_state() (Historical Intelligence) is reused directly.

Every recommendation this module produces is DECISION SUPPORT ONLY, matching
strategy_performance_recommendations' own established contract: apply_human_decision() is the
ONLY function that can move a lifecycle_state forward, requires an explicit approved_by, and
even then only updates THIS tracking table -- it is not, and must never become, a path to
automatically risking capital.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.mt5_strategies.models import ACTIVE_MT5, DISABLED, SHADOW_MT5, STRATEGY_FAMILIES, activation_status
from backend.mt5_strategies.orm import StrategyLifecycleEventORM, StrategyLifecycleStateORM
from backend.mt5_strategies.performance_monitor import (
    DEMOTE_EXPECTANCY_R_THRESHOLD,
    MIN_SAMPLE_FOR_RECOMMENDATION,
    PROMOTE_EXPECTANCY_R_THRESHOLD,
    WINDOW_DAYS,
    _real_trade_stats,
    _shadow_stats,
)
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

RESEARCH = "RESEARCH"
BACKTESTED = "BACKTESTED"
ROBUSTNESS_TESTED = "ROBUSTNESS_TESTED"
SHADOW = "SHADOW"
DEMO = "DEMO"
LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
ACTIVE = "ACTIVE"
DEGRADED = "DEGRADED"
SUSPENDED = "SUSPENDED"
RETIRED = "RETIRED"

PROGRESSION_STATES = (RESEARCH, BACKTESTED, ROBUSTNESS_TESTED, SHADOW, DEMO, LIVE_ELIGIBLE, ACTIVE)
DEGRADATION_STATES = (DEGRADED, SUSPENDED, RETIRED)
ALL_STATES = PROGRESSION_STATES + DEGRADATION_STATES

PROMOTE_RECOMMENDATION = "PROMOTE_RECOMMENDATION"
DEMOTE_RECOMMENDATION = "DEMOTE_RECOMMENDATION"

# What each lifecycle state WOULD map to if the real activation env var matched it exactly --
# observability only (see mapped_activation on the ORM row); never written to any env var by
# this module. DEGRADED deliberately maps to ACTIVE_MT5 (still executing, flagged for review --
# a human decides SUSPENDED/continue, not this module); SUSPENDED/RETIRED map to SHADOW_MT5
# (not DISABLED) matching the Priority 4 precedent of keeping demoted strategies shadow-tracked
# for ongoing research rather than going dark.
_ACTIVATION_MAPPING = {
    RESEARCH: DISABLED, BACKTESTED: DISABLED, ROBUSTNESS_TESTED: DISABLED,
    SHADOW: SHADOW_MT5, DEMO: SHADOW_MT5, LIVE_ELIGIBLE: SHADOW_MT5,
    ACTIVE: ACTIVE_MT5, DEGRADED: ACTIVE_MT5, SUSPENDED: SHADOW_MT5, RETIRED: SHADOW_MT5,
}

# Hysteresis (Part 4 -- "should not flip ACTIVE->SHADOW->ACTIVE because of a few trades"):
# requires this many CONSECUTIVE negative/positive evaluation periods (each period =
# performance_monitor's own WINDOW_DAYS=14d rolling window, re-evaluated whenever
# evaluate_demotion/evaluate_promotion runs) before a degrade/recovery recommendation fires.
# 2 is a deliberately conservative, disclosed-not-hidden choice -- no evidence-based value
# exists for this yet (nothing in the codebase measured optimal hysteresis length), so it is
# documented as a judgment call, not presented as derived.
HYSTERESIS_PERIODS_FOR_DEGRADATION = 2
HYSTERESIS_PERIODS_FOR_RECOVERY = 2

# Historical-robustness evidence (Part 3's "walk-forward/OOS stability", "regime stability",
# "symbol breadth") -- reused as-is from the already-completed, already-accepted 8-year audit
# (docs/strategy-diagnostic-audit-2017-2026.md) rather than recomputed. classification/
# recommendation are the audit's own final calls; walk_forward_pass records whether that
# strategy's OWN best-supported filter (where one exists) passed walk-forward, not the raw
# whole-strategy number, since that is the more decision-relevant fact for promotion purposes.
_AUDIT_CLASSIFICATIONS: dict[str, dict[str, Any]] = {
    "mean_reversion": {"classification": "ROBUST_CORE_EDGE", "audit_recommendation": "KEEP", "walk_forward_pass": True, "edge_stability": "STRONG"},
    "trend_pullback": {"classification": "ROBUST_CORE_EDGE", "audit_recommendation": "KEEP", "walk_forward_pass": True, "edge_stability": "ACCEPTABLE"},
    "smc_continuation": {"classification": "REGIME_DEPENDENT_EDGE", "audit_recommendation": "KEEP_WITH_FILTERS", "walk_forward_pass": True, "edge_stability": "STRONG", "note": "TRENDING-only restriction validated robust in Priority 4; not yet code-implemented"},
    "liquidity_sweep_reversal": {"classification": "REGIME_DEPENDENT_EDGE_LEANING_DECAYED", "audit_recommendation": "RESEARCH_REQUIRED", "walk_forward_pass": True, "edge_stability": "n/a", "note": "4-symbol restriction validated robust in Priority 4; not yet code-implemented"},
    "vwap_reversion": {"classification": "MANAGEMENT_PROBLEM", "audit_recommendation": "IMPROVE_MANAGEMENT", "walk_forward_pass": True, "edge_stability": "ACCEPTABLE", "note": "entry edge confirmed; management forward-validation pending (Priority 2)"},
    "mtfai1": {"classification": "IMPLEMENTATION_PROBLEM_LEANING_NO_CREDIBLE_EDGE", "audit_recommendation": "SHADOW_ONLY", "walk_forward_pass": False, "edge_stability": "FAILED_OOS"},
    "breakout": {"classification": "NO_CREDIBLE_EDGE_DECAYED_EDGE", "audit_recommendation": "RETIRE", "walk_forward_pass": False, "edge_stability": "n/a"},
    "ema_trend": {"classification": "DECAYED_EDGE_NO_CREDIBLE_EDGE", "audit_recommendation": "RETIRE", "walk_forward_pass": False, "edge_stability": "UNSTABLE"},
    "momentum": {"classification": "NO_CREDIBLE_EDGE", "audit_recommendation": "RETIRE", "walk_forward_pass": False, "edge_stability": "n/a"},
    "session_breakout": {"classification": "UNKNOWN_PENDING_REVIEW", "audit_recommendation": "SHADOW_ONLY", "walk_forward_pass": None, "edge_stability": "n/a"},
    "support_resistance_bounce": {"classification": "DECAYED_EDGE", "audit_recommendation": "SHADOW_ONLY", "walk_forward_pass": True, "edge_stability": "STRONG", "note": "6y robust 2018-2024, sharp version-independent breakdown since 2025"},
    "wyckoff": {"classification": "INSUFFICIENT_EVIDENCE", "audit_recommendation": "RESEARCH_ONLY", "walk_forward_pass": None, "edge_stability": "n/a"},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: Any) -> str:
    import json

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def strategy_version_fingerprint(strategy_id: str) -> str | None:
    """Proxy for 'did this strategy's own code change' -- hashes the strategy family's own
    evaluator file content. Deliberately NOT semantic diffing (infeasible to do reliably): a
    changed hash only means SOMETHING changed in that file, never automatically classified as
    cosmetic vs behavioral -- see check_version_consistency/classify_version_change, which
    require an explicit human call on that distinction rather than guessing it."""
    candidates = [
        Path(f"backend/mt5_strategies/families/{strategy_id}.py"),
        Path(f"/app/backend/mt5_strategies/families/{strategy_id}.py"),
    ]
    if strategy_id == "mtfai1":
        # mtfai1 has no families/ file of its own -- it's built inline in autonomous.py (see
        # project memory/Priority 3's own decomposition). Fingerprint that module instead.
        candidates = [Path("backend/brokers/mt5/autonomous.py"), Path("/app/backend/brokers/mt5/autonomous.py")]
    for path in candidates:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:32]
        except OSError:
            continue
    return None


def _default_row(strategy_id: str) -> dict[str, Any]:
    now = utcnow()
    return {
        "strategy_id": strategy_id, "lifecycle_state": RESEARCH, "mapped_activation": _ACTIVATION_MAPPING[RESEARCH],
        "strategy_version_fingerprint": None, "evidence_version_fingerprint": None, "version_status": "CONSISTENT",
        "pending_recommendation": None, "pending_recommendation_reason": None, "pending_recommendation_evidence": {},
        "consecutive_negative_periods": 0, "consecutive_positive_periods": 0, "degraded_since": None,
        "last_transition_at": None, "last_transition_reason": None, "last_evaluated_at": None,
        "created_at": now, "updated_at": now,
    }


def get_state(strategy_id: str) -> dict[str, Any]:
    """Current lifecycle row, or a not-yet-initialized RESEARCH default (never written to the
    DB by a plain read -- see initialize_state for the one-time seed)."""
    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is None:
            return _default_row(strategy_id)
        return {column.name: getattr(row, column.name) for column in StrategyLifecycleStateORM.__table__.columns}


def all_states() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(StrategyLifecycleStateORM).order_by(StrategyLifecycleStateORM.strategy_id.asc()).all()
        return [{column.name: getattr(row, column.name) for column in StrategyLifecycleStateORM.__table__.columns} for row in rows]


def events_for(strategy_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = (
            db.query(StrategyLifecycleEventORM)
            .filter(StrategyLifecycleEventORM.strategy_id == strategy_id)
            .order_by(StrategyLifecycleEventORM.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{column.name: getattr(row, column.name) for column in StrategyLifecycleEventORM.__table__.columns} for row in rows]


def _record_event(db: Any, strategy_id: str, event_type: str, reason: str, *, from_state: str | None = None, to_state: str | None = None, evidence: dict[str, Any] | None = None, approved_by: str | None = None) -> str:
    now = utcnow()
    event_id = "SLE_" + _hash({"strategy": strategy_id, "type": event_type, "time": now.isoformat()})[:40]
    row = StrategyLifecycleEventORM(
        event_id=event_id, strategy_id=strategy_id, event_type=event_type, from_state=from_state, to_state=to_state,
        reason=reason, evidence=evidence or {}, strategy_version_fingerprint=strategy_version_fingerprint(strategy_id),
        approved_by=approved_by, created_at=now,
    )
    db.add(row)
    return event_id


def initialize_state(strategy_id: str, lifecycle_state: str, *, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """One-time seed -- only ever inserts if no row exists yet (never silently overwrites an
    already-initialized strategy's real evidence trail)."""
    if lifecycle_state not in ALL_STATES:
        raise ValueError(f"unknown lifecycle_state: {lifecycle_state}")
    with SessionLocal() as db:
        existing = db.get(StrategyLifecycleStateORM, strategy_id)
        if existing is not None:
            return {column.name: getattr(existing, column.name) for column in StrategyLifecycleStateORM.__table__.columns}
        now = utcnow()
        fingerprint = strategy_version_fingerprint(strategy_id)
        row = StrategyLifecycleStateORM(
            strategy_id=strategy_id, lifecycle_state=lifecycle_state, mapped_activation=_ACTIVATION_MAPPING[lifecycle_state],
            strategy_version_fingerprint=fingerprint, evidence_version_fingerprint=fingerprint, version_status="CONSISTENT",
            last_transition_at=now, last_transition_reason=reason, last_evaluated_at=now, created_at=now, updated_at=now,
        )
        db.add(row)
        _record_event(db, strategy_id, "TRANSITION", reason, from_state=None, to_state=lifecycle_state, evidence=evidence)
        db.commit()
        return get_state(strategy_id)


def check_version_consistency(strategy_id: str) -> dict[str, Any]:
    """Part 5: is the evidence on file still about the code that's actually live? Flags
    REQUIRES_CLASSIFICATION (never auto-decides refactor-vs-behavioral) when the strategy's own
    file hash has changed since evidence_version_fingerprint was last set."""
    state = get_state(strategy_id)
    current_fp = strategy_version_fingerprint(strategy_id)
    evidence_fp = state.get("evidence_version_fingerprint")
    if current_fp is None or evidence_fp is None:
        return {"status": "UNKNOWN", "reason": "fingerprint unavailable (file not found in this environment)"}
    if current_fp == evidence_fp:
        return {"status": "CONSISTENT", "reason": None}
    if state.get("version_status") == "REQUIRES_CLASSIFICATION":
        return {"status": "REQUIRES_CLASSIFICATION", "reason": "already flagged, awaiting human classification"}
    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is not None:
            row.version_status = "REQUIRES_CLASSIFICATION"
            row.strategy_version_fingerprint = current_fp
            row.updated_at = utcnow()
            _record_event(db, strategy_id, "VERSION_FLAGGED", "strategy code changed since evidence was last gathered -- requires human classification (REFACTOR_ONLY vs BEHAVIORAL_CHANGE) before further promotion evidence is trusted", evidence={"previous_fingerprint": evidence_fp, "current_fingerprint": current_fp})
            db.commit()
    return {"status": "REQUIRES_CLASSIFICATION", "reason": "code changed since evidence_version_fingerprint was recorded"}


def classify_version_change(strategy_id: str, *, classification: str, approved_by: str, notes: str | None = None) -> dict[str, Any]:
    """Human call, required before promotion evidence gathered under an old code version can be
    trusted again. REFACTOR_ONLY carries existing evidence/state forward unchanged (just clears
    the flag). BEHAVIORAL_CHANGE resets the lifecycle to BACKTESTED (Part 5 -- 'should not
    inherit full approval from an old version automatically') and requires fresh validation."""
    if classification not in {"REFACTOR_ONLY", "BEHAVIORAL_CHANGE"}:
        raise ValueError("classification must be REFACTOR_ONLY or BEHAVIORAL_CHANGE")
    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is None:
            raise ValueError(f"strategy {strategy_id} has no lifecycle state yet")
        from_state = row.lifecycle_state
        current_fp = strategy_version_fingerprint(strategy_id)
        if classification == "REFACTOR_ONLY":
            row.version_status = "CONSISTENT"
            row.evidence_version_fingerprint = current_fp
            row.updated_at = utcnow()
            _record_event(db, strategy_id, "VERSION_CLASSIFIED", f"classified REFACTOR_ONLY by {approved_by}: {notes or 'no behavioral change, evidence remains valid'}", approved_by=approved_by, evidence={"classification": classification})
        else:
            row.version_status = "BEHAVIORAL_CHANGE_PENDING_REVALIDATION"
            row.lifecycle_state = BACKTESTED
            row.mapped_activation = _ACTIVATION_MAPPING[BACKTESTED]
            row.evidence_version_fingerprint = current_fp
            row.pending_recommendation = None
            row.pending_recommendation_reason = None
            row.consecutive_negative_periods = 0
            row.consecutive_positive_periods = 0
            row.last_transition_at = utcnow()
            row.last_transition_reason = f"BEHAVIORAL_CHANGE classified by {approved_by}: {notes or 'reset to BACKTESTED, fresh validation required'}"
            row.updated_at = utcnow()
            _record_event(db, strategy_id, "VERSION_CLASSIFIED", row.last_transition_reason, from_state=from_state, to_state=BACKTESTED, approved_by=approved_by, evidence={"classification": classification})
        db.commit()
        return get_state(strategy_id)


def gather_evidence(strategy_id: str) -> dict[str, Any]:
    """Part 3/4's evidence base -- pulls ONLY already-cheap, already-established sources (never
    a fresh bootstrap/Monte Carlo run here, see module docstring). Always returns
    missing_evidence explicitly rather than silently omitting a field, so a recommendation
    consumer can see exactly what was and wasn't available."""
    missing: list[str] = []
    window_start = utcnow() - timedelta(days=WINDOW_DAYS)
    current_activation = activation_status(strategy_id)
    if current_activation == ACTIVE_MT5:
        real = _real_trade_stats(strategy_id, window_start)
        forward = {"source": "REAL_TRADES", **real}
    else:
        shadow = _shadow_stats(strategy_id, window_start)
        forward = {"source": "SHADOW_TRACKING", **shadow}
    if not forward.get("sample_size"):
        missing.append("forward_performance:no_closed_trades_in_window")

    historical = _AUDIT_CLASSIFICATIONS.get(strategy_id)
    if historical is None:
        missing.append("historical_audit:not_covered")

    try:
        from backend.brokers.mt5.confidence_calibration import _all_rows, _resolved, _effective_r, _pearson

        rows = [r for r in _all_rows() if _resolved(r) and r.get("strategy") == strategy_id]
        if rows:
            confidences = [r["overall_confidence"] for r in rows]
            outcomes = [_effective_r(r) for r in rows]
            confidence_calibration = {"sample_size": len(rows), "confidence_vs_outcome_correlation": _pearson(confidences, outcomes)}
        else:
            confidence_calibration = None
            missing.append("confidence_calibration:no_resolved_candidates")
    except Exception as exc:
        confidence_calibration = None
        missing.append(f"confidence_calibration:unavailable:{exc.__class__.__name__}")

    try:
        from backend.historical_intelligence import trust_gating

        trust = trust_gating.get_trust_state(strategy_id)
    except Exception as exc:
        trust = None
        missing.append(f"historical_intelligence_trust:unavailable:{exc.__class__.__name__}")

    version = check_version_consistency(strategy_id)
    if version["status"] == "REQUIRES_CLASSIFICATION":
        missing.append("version_status:REQUIRES_CLASSIFICATION -- evidence below may predate a code change, human classification needed before trusting it for promotion")

    return {
        "strategy_id": strategy_id, "evaluated_at": utcnow().isoformat(), "current_activation": current_activation,
        "forward_performance": forward, "historical_audit": historical, "confidence_calibration": confidence_calibration,
        "historical_intelligence_trust": trust, "version": version, "missing_evidence": missing,
    }


def evaluate_promotion(strategy_id: str) -> dict[str, Any]:
    """Part 3 -- evidence-based, never a hard gate on every metric being perfect. Where no
    validated numeric threshold exists for a dimension, that dimension is reported as
    context, not used to block or force the recommendation (see 'do not invent thresholds
    arbitrarily'). The ONLY numeric bars actually enforced here are ones already validated
    elsewhere in this codebase: MIN_SAMPLE_FOR_RECOMMENDATION and
    PROMOTE_EXPECTANCY_R_THRESHOLD, both from performance_monitor.py, reused verbatim."""
    state = get_state(strategy_id)
    evidence = gather_evidence(strategy_id)
    current = state["lifecycle_state"]
    try:
        next_index = PROGRESSION_STATES.index(current) + 1
        next_state = PROGRESSION_STATES[next_index] if next_index < len(PROGRESSION_STATES) else None
    except ValueError:
        next_state = None  # currently in a degradation state -- promotion evaluation doesn't apply; see evaluate_demotion's own recovery path

    if next_state is None:
        return {"recommendation": None, "reason": "not in a progression state (either already ACTIVE or currently degraded -- see evaluate_demotion for recovery)", "evidence": evidence}
    if evidence["version"]["status"] == "REQUIRES_CLASSIFICATION":
        return {"recommendation": None, "reason": "version changed since evidence was gathered -- classify_version_change() required first", "evidence": evidence}

    forward = evidence["forward_performance"]
    sample = forward.get("sample_size") or 0
    expectancy = forward.get("expectancy_r")
    if sample < MIN_SAMPLE_FOR_RECOMMENDATION:
        return {"recommendation": None, "confidence": "LOW", "reason": f"INSUFFICIENT_SAMPLE: {sample} closed trades in the last {WINDOW_DAYS}d (need >= {MIN_SAMPLE_FOR_RECOMMENDATION})", "evidence": evidence}
    if expectancy is None or expectancy < PROMOTE_EXPECTANCY_R_THRESHOLD:
        return {"recommendation": None, "confidence": "MEDIUM", "reason": f"forward expectancy {expectancy} does not clear PROMOTE_EXPECTANCY_R_THRESHOLD ({PROMOTE_EXPECTANCY_R_THRESHOLD})", "evidence": evidence}

    historical = evidence["historical_audit"] or {}
    context_notes = []
    if historical.get("walk_forward_pass") is False:
        context_notes.append("historical audit walk-forward FAILED for this strategy -- forward evidence alone is carrying this recommendation")
    if not evidence["confidence_calibration"]:
        context_notes.append("no confidence-calibration data available yet")
    confidence_label = "HIGH" if (sample >= MIN_SAMPLE_FOR_RECOMMENDATION * 2 and historical.get("walk_forward_pass")) else "MEDIUM"

    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is not None:
            row.pending_recommendation = PROMOTE_RECOMMENDATION
            row.pending_recommendation_reason = f"forward sample={sample}, expectancy_r={expectancy:.4f} clears PROMOTE_EXPECTANCY_R_THRESHOLD; candidate next state: {next_state}"
            row.pending_recommendation_evidence = evidence
            row.last_evaluated_at = utcnow()
            row.updated_at = utcnow()
            _record_event(db, strategy_id, "PROMOTE_RECOMMENDATION", row.pending_recommendation_reason, from_state=current, to_state=next_state, evidence=evidence)
            db.commit()
    return {"recommendation": PROMOTE_RECOMMENDATION, "candidate_next_state": next_state, "confidence": confidence_label, "reason": f"sample={sample}, expectancy_r={expectancy:.4f} >= {PROMOTE_EXPECTANCY_R_THRESHOLD}", "context_notes": context_notes, "evidence": evidence}


def evaluate_demotion(strategy_id: str) -> dict[str, Any]:
    """Part 4 -- hysteresis via consecutive_negative_periods, persisted so a single bad
    14-day window can never alone trigger DEGRADED; requires HYSTERESIS_PERIODS_FOR_DEGRADATION
    consecutive negative evaluations. Symmetric recovery counter for DEGRADED -> back to its
    prior progression state."""
    state = get_state(strategy_id)
    evidence = gather_evidence(strategy_id)
    current = state["lifecycle_state"]
    forward = evidence["forward_performance"]
    sample = forward.get("sample_size") or 0
    expectancy = forward.get("expectancy_r")

    if sample < MIN_SAMPLE_FOR_RECOMMENDATION:
        return {"recommendation": None, "reason": f"INSUFFICIENT_SAMPLE: {sample} closed trades (need >= {MIN_SAMPLE_FOR_RECOMMENDATION}) -- no demotion signal, but also no confirmation", "evidence": evidence}

    negative = expectancy is not None and expectancy < DEMOTE_EXPECTANCY_R_THRESHOLD
    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is None:
            return {"recommendation": None, "reason": "strategy has no lifecycle state yet", "evidence": evidence}
        if negative:
            row.consecutive_negative_periods = (row.consecutive_negative_periods or 0) + 1
            row.consecutive_positive_periods = 0
        else:
            row.consecutive_positive_periods = (row.consecutive_positive_periods or 0) + 1
            row.consecutive_negative_periods = 0
        row.last_evaluated_at = utcnow()
        row.updated_at = utcnow()

        result: dict[str, Any]
        if negative and row.consecutive_negative_periods >= HYSTERESIS_PERIODS_FOR_DEGRADATION and current not in DEGRADATION_STATES:
            row.pending_recommendation = DEMOTE_RECOMMENDATION
            row.pending_recommendation_reason = f"{row.consecutive_negative_periods} consecutive negative {WINDOW_DAYS}d windows (expectancy_r={expectancy:.4f} < {DEMOTE_EXPECTANCY_R_THRESHOLD})"
            row.pending_recommendation_evidence = evidence
            if row.degraded_since is None:
                row.degraded_since = utcnow()
            _record_event(db, strategy_id, "DEMOTE_RECOMMENDATION", row.pending_recommendation_reason, from_state=current, to_state=DEGRADED, evidence=evidence)
            result = {"recommendation": DEMOTE_RECOMMENDATION, "candidate_next_state": DEGRADED, "reason": row.pending_recommendation_reason, "evidence": evidence}
        elif not negative and current in DEGRADATION_STATES and row.consecutive_positive_periods >= HYSTERESIS_PERIODS_FOR_RECOVERY:
            row.pending_recommendation = PROMOTE_RECOMMENDATION
            row.pending_recommendation_reason = f"{row.consecutive_positive_periods} consecutive positive {WINDOW_DAYS}d windows since {current} -- recovery candidate"
            row.pending_recommendation_evidence = evidence
            _record_event(db, strategy_id, "PROMOTE_RECOMMENDATION", row.pending_recommendation_reason, from_state=current, to_state=DEMO, evidence=evidence)
            result = {"recommendation": PROMOTE_RECOMMENDATION, "candidate_next_state": DEMO, "reason": row.pending_recommendation_reason, "evidence": evidence, "note": "recovery from a degraded state re-enters at DEMO, not directly ACTIVE -- fresh forward evidence required either way"}
        else:
            result = {"recommendation": None, "reason": f"no hysteresis threshold crossed yet (negative_periods={row.consecutive_negative_periods}, positive_periods={row.consecutive_positive_periods})", "evidence": evidence}
        db.commit()
    return result


def apply_human_decision(strategy_id: str, *, decision: str, approved_by: str, notes: str | None = None) -> dict[str, Any]:
    """The ONLY function that can move lifecycle_state forward. decision in {"APPROVE",
    "REJECT"}. Approving moves lifecycle_state to the pending recommendation's candidate next
    state (recorded when the recommendation was raised) -- this NEVER writes
    MT5_STRATEGY_ACTIVATION_<ID>; a lifecycle_state of ACTIVE is a statement that evidence
    supports live capital, not the deploy that risks it. That stays the existing, separate,
    manual docker-compose/.env change, same as every activation decision made in Priorities
    3-4."""
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    with SessionLocal() as db:
        row = db.get(StrategyLifecycleStateORM, strategy_id)
        if row is None or not row.pending_recommendation:
            raise ValueError(f"strategy {strategy_id} has no pending recommendation to act on")
        from_state = row.lifecycle_state
        pending = row.pending_recommendation
        reason = row.pending_recommendation_reason or ""
        evidence = row.pending_recommendation_evidence or {}
        if decision == "REJECT":
            row.pending_recommendation = None
            row.pending_recommendation_reason = None
            row.updated_at = utcnow()
            _record_event(db, strategy_id, "HUMAN_REJECTED", f"rejected by {approved_by}: {notes or reason}", from_state=from_state, to_state=from_state, approved_by=approved_by, evidence=evidence)
            db.commit()
            return get_state(strategy_id)

        # candidate_next_state lived on evaluate_promotion/demotion's RESULT dict, not on the
        # persisted evidence -- re-derive it the same way those functions did, so approval never
        # depends on the caller having kept the original result around.
        if pending == PROMOTE_RECOMMENDATION:
            if from_state in DEGRADATION_STATES:
                to_state = DEMO
            else:
                idx = PROGRESSION_STATES.index(from_state) + 1 if from_state in PROGRESSION_STATES else None
                to_state = PROGRESSION_STATES[idx] if idx is not None and idx < len(PROGRESSION_STATES) else from_state
        elif pending == DEMOTE_RECOMMENDATION:
            to_state = DEGRADED

        row.lifecycle_state = to_state
        row.mapped_activation = _ACTIVATION_MAPPING[to_state]
        row.pending_recommendation = None
        row.pending_recommendation_reason = None
        row.consecutive_negative_periods = 0
        row.consecutive_positive_periods = 0
        if to_state not in DEGRADATION_STATES:
            row.degraded_since = None
        row.last_transition_at = utcnow()
        row.last_transition_reason = f"{pending} approved by {approved_by}: {notes or reason}"
        row.updated_at = utcnow()
        _record_event(db, strategy_id, "HUMAN_APPROVED", row.last_transition_reason, from_state=from_state, to_state=to_state, approved_by=approved_by, evidence=evidence)
        db.commit()
        return get_state(strategy_id)


def explain(strategy_id: str) -> dict[str, Any]:
    """Part 13 -- 'no opaque status changes'. Full explanation of the current state: what it
    is, when/why it last changed, what's pending, and the recent audit trail."""
    state = get_state(strategy_id)
    return {
        "strategy_id": strategy_id,
        "lifecycle_state": state["lifecycle_state"],
        "mapped_activation": state["mapped_activation"],
        "actual_activation": activation_status(strategy_id),
        "version_status": state["version_status"],
        "last_transition_at": state["last_transition_at"],
        "last_transition_reason": state["last_transition_reason"],
        "pending_recommendation": state["pending_recommendation"],
        "pending_recommendation_reason": state["pending_recommendation_reason"],
        "consecutive_negative_periods": state["consecutive_negative_periods"],
        "consecutive_positive_periods": state["consecutive_positive_periods"],
        "recent_events": events_for(strategy_id, limit=10),
    }

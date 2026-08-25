"""Bensim -- Adaptive Manager V3 continuation, Part 9: strategy-level automatic DEMO demotion.

Deliberately SEPARATE from backend.mt5_strategies.circuit_breaker (that module is explicitly
scoped to operational malfunction only -- "never a strategy that is simply losing trades,
trading losses are expected and must never trip this", per its own module docstring) and from
backend.mt5_strategies.lifecycle (that system's apply_human_decision() is the ONLY state-mover
and requires approved_by -- deliberately human-gated, never touches capital automatically). This
module is the third, genuinely automatic layer the user explicitly asked for: real performance
deterioration, after sufficient real evidence, demotes ACTIVE_MT5 -> SHADOW_MT5 with no human
step -- but ONLY ever demotes, never promotes (Part 9: "Never automatically promote SHADOW back
to ACTIVE"), and never touches LIVE configuration (DEMO-only by construction: real-trade stats
are pulled from the DEMO accounts' own MT5CandidateEvaluationORM rows, nothing here ever writes
to a live-account-scoped table or config key).

Conservative-by-design: requires a MINIMUM real closed-trade sample (default 30) AND materially
bad evidence (not tiny deviations around zero) on at least one of expectancy/PF/drawdown, AND
requires the RECENT window to also be bad (not just one early bad patch dragging down an
all-time average) before tripping. Every threshold is env-configurable and every trip is logged
with the exact evidence that caused it (MT5DemoSafetyCircuitStateORM.evidence, JSON).

Wired into activation_status() (models.py) at the SAME precedence tier as the operational
circuit breaker (checked right after it, before the env override) -- so a trip forces SHADOW_MT5
even if an operator's MT5_STRATEGY_ACTIVATION_<ID> env var still says ACTIVE_MT5, exactly like
circuit_breaker already does for DISABLED. Evaluated periodically by the existing strategy-
performance-monitor scheduler (performance_monitor.py's StrategyPerformanceMonitor.run_once) --
no new scheduler.
"""
from __future__ import annotations

import logging
import os
import statistics as st
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String

from backend.shared.db import Base, SessionLocal

logger = logging.getLogger(__name__)


class MT5DemoSafetyCircuitStateORM(Base):
    """One row per strategy. tripped=True means activation_status() forces SHADOW_MT5 for this
    strategy regardless of its env override -- see models.py::activation_status. Never deleted;
    a strategy's trip history stays visible even after a human manually re-promotes it (which
    this module never does itself -- resetting `tripped` requires an explicit operator action,
    see reset_trip())."""

    __tablename__ = "mt5_demo_safety_circuit_state"

    strategy_id = Column(String(64), primary_key=True)
    tripped = Column(Boolean, nullable=False, default=False, index=True)
    tripped_at = Column(DateTime(timezone=True), nullable=True)
    tripped_reason = Column(String(2000), nullable=True)
    evidence = Column(JSON, nullable=True)
    sample_size = Column(Integer, nullable=True)
    expectancy_r = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    max_drawdown_r = Column(Float, nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    reset_at = Column(DateTime(timezone=True), nullable=True)
    reset_by = Column(String(120), nullable=True)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Conservative defaults -- deliberately requiring MATERIALLY bad evidence, not a marginal dip
# around zero (explicit instruction: "require materially bad evidence rather than tiny
# deviations around zero"). MIN_SAMPLE default sits between the user's own example bounds
# (30-50); a strategy this new to real DEMO execution reaching even 30 real closed trades
# already represents real, meaningful forward evidence, not noise.
MIN_SAMPLE = _env_int("MT5_DEMO_SAFETY_MIN_SAMPLE", 30)
EXPECTANCY_THRESHOLD_R = _env_float("MT5_DEMO_SAFETY_EXPECTANCY_THRESHOLD_R", -0.15)
PF_THRESHOLD = _env_float("MT5_DEMO_SAFETY_PF_THRESHOLD", 0.70)
MAX_DD_THRESHOLD_R = _env_float("MT5_DEMO_SAFETY_MAX_DD_THRESHOLD_R", -8.0)
RECENT_WINDOW_N = _env_int("MT5_DEMO_SAFETY_RECENT_WINDOW_N", 20)
CIRCUIT_ENABLED = os.getenv("MT5_DEMO_SAFETY_CIRCUIT_ENABLED", "true").strip().lower() not in {"false", "0", "off", "no"}


@dataclass(frozen=True)
class SafetyEvaluation:
    strategy_id: str
    sample_size: int
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_r: float | None
    recent_expectancy_r: float | None
    should_trip: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _stats(vals: list[float]) -> dict[str, Any]:
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "pf": None, "max_dd_r": None}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gw, gl = sum(wins) if wins else 0.0, abs(sum(losses)) if losses else 0.0
    equity, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return {"n": n, "expectancy_r": round(st.fmean(vals), 4), "pf": round(gw / gl, 3) if gl else None, "max_dd_r": round(dd, 3)}


def evaluate_strategy(strategy_id: str, *, since: datetime | None = None) -> SafetyEvaluation:
    """Pure evaluation, no side effects -- pulls REAL executed+closed trades for `strategy_id`
    (optionally restricted to `since`, e.g. a post-reactivation cutover, Part 10) and decides
    whether the evidence is bad enough, on a large enough sample, to justify tripping. Never
    raises; missing data or import failures degrade to should_trip=False (fail toward keeping
    the strategy active, never toward an accidental demotion from a data glitch)."""
    try:
        from backend.brokers.mt5.orm import MT5CandidateEvaluationORM

        with SessionLocal() as db:
            query = db.query(MT5CandidateEvaluationORM).filter(
                MT5CandidateEvaluationORM.strategy == strategy_id,
                MT5CandidateEvaluationORM.outcome_type == "EXECUTED",
            )
            if since is not None:
                query = query.filter(MT5CandidateEvaluationORM.created_at >= since)
            rows = query.order_by(MT5CandidateEvaluationORM.created_at.asc()).all()
    except Exception as exc:
        logger.warning("MT5 demo safety circuit: evaluation query failed for %s (fail-open, no trip): %s", strategy_id, exc.__class__.__name__)
        return SafetyEvaluation(strategy_id, 0, None, None, None, None, False, "EVALUATION_UNAVAILABLE", {})

    def r_of(row: Any) -> float | None:
        return row.realized_r if row.realized_r is not None else row.hypothetical_r

    vals = [r_of(r) for r in rows if r_of(r) is not None]
    overall = _stats(vals)
    recent_vals = vals[-RECENT_WINDOW_N:] if len(vals) >= RECENT_WINDOW_N else []
    recent = _stats(recent_vals) if recent_vals else {"n": 0, "expectancy_r": None}

    n = overall["n"]
    if n < MIN_SAMPLE:
        return SafetyEvaluation(strategy_id, n, overall["expectancy_r"], overall["pf"], overall["max_dd_r"], recent.get("expectancy_r"), False, f"INSUFFICIENT_SAMPLE ({n}/{MIN_SAMPLE})", {"overall": overall, "recent": recent})

    bad_dimensions = []
    if overall["expectancy_r"] is not None and overall["expectancy_r"] < EXPECTANCY_THRESHOLD_R:
        bad_dimensions.append(f"expectancy_r={overall['expectancy_r']}<{EXPECTANCY_THRESHOLD_R}")
    if overall["pf"] is not None and overall["pf"] < PF_THRESHOLD:
        bad_dimensions.append(f"pf={overall['pf']}<{PF_THRESHOLD}")
    if overall["max_dd_r"] is not None and overall["max_dd_r"] < MAX_DD_THRESHOLD_R:
        bad_dimensions.append(f"max_dd_r={overall['max_dd_r']}<{MAX_DD_THRESHOLD_R}")

    # Recent-window confirmation: the deterioration must still be visible in the most recent
    # RECENT_WINDOW_N trades too, not just baked into an all-time average by an early bad patch
    # the strategy has since recovered from -- a real "recent-vs-longer-window degradation"
    # check, deliberately making the circuit HARDER to trip, not easier.
    recent_confirms = recent.get("n", 0) >= RECENT_WINDOW_N and recent.get("expectancy_r") is not None and recent["expectancy_r"] < EXPECTANCY_THRESHOLD_R

    should_trip = bool(bad_dimensions) and recent_confirms
    if should_trip:
        reason = f"MATERIALLY_BAD_EVIDENCE: {', '.join(bad_dimensions)}; recent {RECENT_WINDOW_N}-trade expectancy={recent.get('expectancy_r')} also below threshold"
    elif bad_dimensions and not recent_confirms:
        reason = f"OVERALL_BAD_BUT_RECENT_WINDOW_NOT_CONFIRMED (overall: {', '.join(bad_dimensions)}; recent_n={recent.get('n', 0)}, recent_expectancy={recent.get('expectancy_r')})"
    else:
        reason = "WITHIN_ACCEPTABLE_RANGE"

    return SafetyEvaluation(
        strategy_id, n, overall["expectancy_r"], overall["pf"], overall["max_dd_r"], recent.get("expectancy_r"),
        should_trip, reason, {"overall": overall, "recent": recent, "thresholds": {"min_sample": MIN_SAMPLE, "expectancy_r": EXPECTANCY_THRESHOLD_R, "pf": PF_THRESHOLD, "max_dd_r": MAX_DD_THRESHOLD_R, "recent_window_n": RECENT_WINDOW_N}},
    )


def is_tripped(strategy_id: str) -> bool:
    """Read-only check for activation_status() -- must be cheap and never raise (fails open to
    False, i.e. no demotion, on any DB error, matching every other fail-safe read in this
    codebase's activation path)."""
    if not CIRCUIT_ENABLED:
        return False
    try:
        with SessionLocal() as db:
            row = db.get(MT5DemoSafetyCircuitStateORM, strategy_id)
            return bool(row and row.tripped)
    except Exception as exc:
        logger.warning("MT5 demo safety circuit: is_tripped check failed for %s (fail-open, false): %s", strategy_id, exc.__class__.__name__)
        return False


def apply_evaluation(strategy_id: str, evaluation: SafetyEvaluation) -> bool:
    """Persists `evaluation` and, if it says should_trip and the strategy isn't ALREADY tripped,
    flips the trip flag and logs exactly why. Returns True if this call newly tripped the
    circuit (for logging/alerting by the caller), False otherwise -- including when it was
    already tripped (idempotent, never re-logs the same trip)."""
    now = datetime.now(timezone.utc)
    newly_tripped = False
    with SessionLocal() as db:
        row = db.get(MT5DemoSafetyCircuitStateORM, strategy_id)
        if row is None:
            row = MT5DemoSafetyCircuitStateORM(strategy_id=strategy_id, tripped=False)
            db.add(row)
        row.sample_size = evaluation.sample_size
        row.expectancy_r = evaluation.expectancy_r
        row.profit_factor = evaluation.profit_factor
        row.max_drawdown_r = evaluation.max_drawdown_r
        row.last_evaluated_at = now
        row.evidence = evaluation.evidence
        if evaluation.should_trip and not row.tripped:
            row.tripped = True
            row.tripped_at = now
            row.tripped_reason = evaluation.reason
            newly_tripped = True
            logger.warning("MT5 demo safety circuit TRIPPED for %s: %s", strategy_id, evaluation.reason)
        db.commit()
    return newly_tripped


def reset_trip(strategy_id: str, *, reset_by: str) -> bool:
    """The ONLY way a tripped circuit clears -- always an explicit human/operator action (mirrors
    lifecycle.py's own apply_human_decision() contract), never automatic. Returns False if the
    strategy wasn't tripped (no-op)."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(MT5DemoSafetyCircuitStateORM, strategy_id)
        if row is None or not row.tripped:
            return False
        row.tripped = False
        row.reset_at = now
        row.reset_by = reset_by
        db.commit()
    logger.warning("MT5 demo safety circuit manually RESET for %s by %s", strategy_id, reset_by)
    return True


def evaluate_and_apply_all(strategy_ids: list[str], *, cutovers: dict[str, datetime] | None = None) -> dict[str, dict[str, Any]]:
    """Convenience entry point for the scheduler (performance_monitor.py) -- evaluates and
    applies for every strategy in `strategy_ids`, using each strategy's own cutover from
    `cutovers` (Part 10: primarily use POST_ACTIVATION evidence) when available. Never raises;
    one strategy's failure doesn't block the others."""
    results: dict[str, dict[str, Any]] = {}
    for strategy_id in strategy_ids:
        try:
            since = (cutovers or {}).get(strategy_id)
            evaluation = evaluate_strategy(strategy_id, since=since)
            newly_tripped = apply_evaluation(strategy_id, evaluation)
            results[strategy_id] = {"should_trip": evaluation.should_trip, "newly_tripped": newly_tripped, "reason": evaluation.reason, "sample_size": evaluation.sample_size}
        except Exception as exc:
            logger.warning("MT5 demo safety circuit: evaluate_and_apply_all failed for %s: %s", strategy_id, exc.__class__.__name__)
            results[strategy_id] = {"error": exc.__class__.__name__}
    return results

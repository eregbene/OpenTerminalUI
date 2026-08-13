"""Confidence Validation & Calibration layer -- Part 1 (persist every fully-scored
candidate) and the write side of Parts 2/3 (shadow + executed outcome recording).

This module is deliberately separate from backend/brokers/mt5/persistence.py: that module
backs the trading engine's own operational bookkeeping (upserted per cycle), while this one
is an append-only historical dataset for calibration analytics. Nothing here can affect the
live trading decision -- capture_cycle_candidate_evaluations() is called AFTER a cycle's
decision is already made (from MT5AutonomousTradingService._record_cycle), and every call is
wrapped by the caller in a try/except so a persistence failure here can never break a live
trading cycle.

Lookahead-bias discipline (Part 10): every field written by capture_cycle_candidate_
evaluations() is information already known at, or before, the moment the cycle's decision was
made -- confidence components, proposed entry/SL/TP, portfolio state, rejection reasons. Only
record_shadow_outcome()/record_executed_outcome() touch the outcome_* columns, and they are
called strictly later (by backend/brokers/mt5/outcome_resolver.py), after real time has
elapsed and new market data exists. Decision columns are never revisited by either function.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# How long a non-executed (shadow) candidate is tracked against live market data before being
# finalized as EXPIRED_NO_TOUCH if neither the proposed TP nor SL has been touched. Chosen to
# comfortably exceed the typical resolution horizon of an M5-cycle swing trade without tracking
# stale setups forever. Analytics-only constant -- does not affect the live trading engine.
SHADOW_TRACKING_WINDOW = timedelta(days=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def capture_cycle_candidate_evaluations(result: dict[str, Any]) -> int:
    """Persist an immutable evaluation row for every candidate in `result["candidates"]` that
    reached full confidence scoring (has a "trade_confidence" key) -- selected, lower-ranked,
    or rejected alike. Idempotent per candidate_id: a candidate_id that already has a row is
    left untouched (first write wins), so this can be safely called even if a cycle result is
    ever reprocessed. Returns the number of new rows written."""
    cycle_id = result.get("cycle_id")
    if not cycle_id:
        return 0
    account_id = str(result.get("account_id") or _account_id_from_cycle_id(str(cycle_id)))
    candidates = [c for c in (result.get("candidates") or []) if isinstance(c, dict) and "trade_confidence" in c]
    if not candidates:
        return 0
    winner = result.get("winner") or {}
    winner_id = winner.get("candidate_id")
    status = str(result.get("status") or "")
    trade = result.get("trade") or {}
    submission = trade.get("submission") or {}
    confirmation_gate = result.get("confirmation_gate") or {}
    extra_rejection_by_status = {
        "SKIPPED_CONTEXT_RISK": "CONTEXT_RISK",
        "SKIPPED_ECONOMIC_RISK": "ECONOMIC_RISK",
        "SKIPPED_STALE_RISK_REWARD": "STALE_SIGNAL",
    }
    portfolio_state, open_positions, correlated_exposure = _decision_time_portfolio_context(account_id)

    written = 0
    now = utcnow()
    with SessionLocal() as db:
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            if not candidate_id:
                continue
            if db.query(MT5CandidateEvaluationORM.evaluation_id).filter(MT5CandidateEvaluationORM.candidate_id == candidate_id).first():
                continue  # Immutable: a row already exists for this candidate_id -- never overwritten.

            is_winner = bool(winner_id and candidate_id == winner_id)
            selected = is_winner and status != "NO_TRADE"
            rejection_reasons = sorted(set(candidate.get("rejection_reasons") or []))
            if is_winner and status in extra_rejection_by_status:
                rejection_reasons = sorted(set(rejection_reasons + [extra_rejection_by_status[status]]))
            eligible_for_execution = "BELOW_CONFIDENCE_THRESHOLD" not in rejection_reasons

            confidence = candidate.get("trade_confidence") or {}
            context = candidate.get("context") or {}

            outcome_type = "SHADOW"
            broker_ticket = None
            actual_entry = None
            if is_winner and submission.get("status") == "ACCEPTED":
                outcome_type = "EXECUTED"
                broker_ticket = str(submission.get("order_ticket")) if submission.get("order_ticket") is not None else None
                actual_entry = _float((trade.get("intent") or {}).get("entry_price"))
                if not broker_ticket:
                    outcome_type = "SHADOW"  # No ticket to link against -- fall back to shadow tracking.

            row = MT5CandidateEvaluationORM(evaluation_id=_hash({"candidate_id": candidate_id})[:32])
            row.cycle_id = str(cycle_id)
            row.account_id = account_id
            row.candidate_id = str(candidate_id)
            row.created_at = now
            row.market_data_as_of = _parse_iso(context.get("timestamp"))
            row.symbol = str(candidate.get("canonical_pair") or candidate.get("symbol") or "").upper()
            row.broker_symbol = str(candidate.get("broker_symbol") or row.symbol).upper()
            row.direction = str(candidate.get("direction") or "").upper()
            row.timeframe = context.get("timeframe")
            row.strategy = candidate.get("strategy") or context.get("strategy_id") or context.get("strategy")
            row.market_regime = candidate.get("market_regime") or context.get("regime") or context.get("market_regime")
            row.session = candidate.get("session") or context.get("session")
            row.strategy_family = context.get("strategy_family")
            row.contributing_strategies = context.get("contributing_strategies") or []
            row.contributing_families = context.get("contributing_families") or []
            row.multi_strategy_confirmation = bool(context.get("multi_strategy_confirmation") or False)
            # DEMO-only MTFAI1 entry-quality experiment: only meaningful for the one executed
            # candidate this cycle when it's MTFAI1 -- confirmation_gate is a single, cycle-level
            # verdict about whichever MTFAI1 candidate the gate actually evaluated as `best`,
            # so it is only attached to that same winning, executed row (never guessed for any
            # other candidate, including non-winning or non-executed MTFAI1 rows this cycle).
            if is_winner and outcome_type == "EXECUTED" and row.strategy == "mtfai1" and confirmation_gate.get("applicable"):
                row.mtfai1_confirmed = confirmation_gate.get("confirmed")
                row.mtfai1_confirming_strategy_ids = confirmation_gate.get("confirming_strategy_ids") or []
            row.conflict_state = context.get("conflict_state") or "NONE"
            row.htf_direction_h4 = context.get("htf_trend_h4") or (context.get("smc_evidence") or {}).get("htf_direction_h4")
            row.htf_direction_h1 = (context.get("smc_evidence") or {}).get("htf_direction_h1")
            row.raw_signal_strength = _float(context.get("score"))
            row.smc_evidence = context.get("smc_evidence") or {}
            row.strategy_evidence = context.get("strategy_evidence") or {}
            row.overall_confidence = float(confidence.get("overall_score") or 0)
            row.confidence_band = str(confidence.get("band") or "reject")
            row.components = confidence.get("components") or []
            row.raw_trend_score = _float(candidate.get("raw_trend_score") or candidate.get("screening_score"))
            row.rule_version = confidence.get("rule_version")
            row.rank = candidate.get("rank")
            row.selected = selected
            row.eligible_for_execution = eligible_for_execution
            row.rejection_reasons = rejection_reasons
            row.proposed_entry = _float(candidate.get("entry") or context.get("entry"))
            row.proposed_stop_loss = _float(candidate.get("stop_loss"))
            row.proposed_take_profit = _float(candidate.get("take_profit"))
            row.initial_reward_risk = _float(context.get("risk_reward"))
            row.spread = _float(context.get("spread"))
            row.atr = _float(context.get("atr"))
            row.portfolio_state_snapshot = portfolio_state
            row.open_positions_snapshot = open_positions
            row.correlated_exposure_snapshot = correlated_exposure
            row.outcome_type = outcome_type
            row.broker_ticket = broker_ticket
            row.actual_entry = actual_entry
            row.outcome_status = "PENDING"
            row.expiry_at = now + SHADOW_TRACKING_WINDOW
            db.add(row)
            written += 1
        if written:
            db.commit()
    return written


def _decision_time_portfolio_context(account_id: str | None = None) -> tuple[dict[str, Any], list[Any], dict[str, Any]]:
    """Best-effort, decision-time-only snapshot of portfolio state. Never raises -- a
    portfolio-context lookup failure must not prevent candidate evaluations from being
    recorded (the confidence/rank/rejection data is far more valuable than this context)."""
    try:
        from backend.portfolio_execution.service import portfolio_manager

        latest = portfolio_manager.latest_snapshot(account_id) or {}
        exposure = portfolio_manager.exposure(account_id)
        open_positions = latest.get("positions") or latest.get("open_positions") or []
        state_keys = ("equity", "balance", "open_risk", "margin_utilization", "worst_case_loss", "protection_state")
        portfolio_state = {key: latest.get(key) for key in state_keys}
        return portfolio_state, open_positions, exposure
    except Exception as exc:
        logger.warning("MT5 candidate evaluation: portfolio context unavailable: %s", exc.__class__.__name__)
        return {}, [], {}


def _account_id_from_cycle_id(cycle_id: str) -> str:
    return cycle_id.split(":", 1)[0] if ":" in cycle_id else "demo_10k"


def record_shadow_outcome(candidate_id: str, **fields: Any) -> bool:
    """Update ONLY the outcome_* columns of an existing evaluation row for a non-executed
    candidate. Never touches any decision-time column. Returns False if no row exists yet."""
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter(MT5CandidateEvaluationORM.candidate_id == candidate_id).first()
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        row.outcome_updated_at = utcnow()
        db.commit()
    return True


def record_executed_outcome(candidate_id: str, **fields: Any) -> bool:
    """Update ONLY the outcome_* columns of an existing evaluation row for an executed
    candidate, using data already computed by adaptive trade management (AdaptivePositionStateORM).
    Never touches any decision-time column. Returns False if no row exists yet."""
    return record_shadow_outcome(candidate_id, **fields)


def pending_shadow_candidates(*, limit: int = 200) -> list[dict[str, Any]]:
    """Non-executed candidate evaluations still awaiting outcome resolution (Part 2)."""
    with SessionLocal() as db:
        rows = (
            db.query(MT5CandidateEvaluationORM)
            .filter(MT5CandidateEvaluationORM.outcome_type == "SHADOW", MT5CandidateEvaluationORM.outcome_status == "PENDING")
            .order_by(MT5CandidateEvaluationORM.created_at.asc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(row) for row in rows]


def pending_executed_candidates(*, limit: int = 200) -> list[dict[str, Any]]:
    """Executed candidate evaluations still awaiting a link to their closed position (Part 3)."""
    with SessionLocal() as db:
        rows = (
            db.query(MT5CandidateEvaluationORM)
            .filter(MT5CandidateEvaluationORM.outcome_type == "EXECUTED", MT5CandidateEvaluationORM.outcome_status == "PENDING")
            .order_by(MT5CandidateEvaluationORM.created_at.asc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: MT5CandidateEvaluationORM) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in MT5CandidateEvaluationORM.__table__.columns}

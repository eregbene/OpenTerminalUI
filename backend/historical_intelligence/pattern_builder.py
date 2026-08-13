"""Orchestrates fingerprint + outcome generation for real historical setups (Parts 1-3).

Never fabricates a setup: every fingerprint built here traces back to either a real live
MT5CandidateEvaluationORM row (via replay_for_evaluation's source hierarchy) or a real candidate
produced by broad point-in-time replay (replay_at) at a real historical instant over real
ingested bars. This module is the only place that writes HistoricalPatternFingerprintORM rows --
fingerprint.py itself is a pure function with no DB access.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.historical_intelligence import outcomes
from backend.historical_intelligence.fingerprint import build_fingerprint
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
from backend.historical_intelligence.replay import replay_for_evaluation
from backend.shared.db import SessionLocal


def _fingerprint_id(evaluation_id: str, strategy_id: str) -> str:
    return "HPF_" + hashlib.sha256(f"{evaluation_id}:{strategy_id}".encode()).hexdigest()[:40]


def _extract_candidate_geometry(replay_result: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    if strategy_id == "mtfai1":
        mtfai1 = replay_result.get("mtfai1") or {}
        if mtfai1.get("direction") in (None, "NO_TRADE"):
            return None
        return {
            "entry": mtfai1.get("entry") or mtfai1.get("proposed_entry"),
            "stop_loss": mtfai1.get("stop_loss") or mtfai1.get("proposed_stop_loss"),
            "take_profit": mtfai1.get("take_profit") or mtfai1.get("proposed_take_profit"),
        }
    for candidate in replay_result.get("families_candidates") or []:
        candidate_strategy = candidate.get("context", {}).get("strategy_id") or candidate.get("strategy_id")
        if candidate_strategy != strategy_id:
            continue
        return {
            "entry": candidate.get("entry") or candidate.get("proposed_entry"),
            "stop_loss": candidate.get("stop_loss") or candidate.get("proposed_stop_loss"),
            "take_profit": candidate.get("take_profit") or candidate.get("proposed_take_profit"),
        }
    return None


async def build_fingerprint_for_evaluation(evaluation_id: str, *, provider: str = "MT5", label_outcome: bool = True) -> dict[str, Any] | None:
    """Replays a REAL live evaluation and, if the replay reproduces a candidate for that same
    strategy (i.e. the setup genuinely exists in the historical record, not just "live said so"),
    builds and persists its fingerprint + outcome label. Returns None when replay status isn't OK
    or the strategy didn't fire in replay -- callers (bulk generation) simply skip these rather
    than fabricate a fingerprint from live-only data replay couldn't reproduce."""
    with SessionLocal() as db:
        live_row = db.get(MT5CandidateEvaluationORM, evaluation_id)
        if live_row is None:
            return None
        strategy_id = live_row.strategy or ""
        contributing = live_row.contributing_strategies or ([strategy_id] if strategy_id else [])
        strategy_family = live_row.strategy_family
        confidence_band = live_row.confidence_band
        canonical_symbol, broker_symbol = live_row.symbol, live_row.broker_symbol

    replay_result = await replay_for_evaluation(evaluation_id, provider=provider)
    if replay_result.get("status") != "OK":
        return None
    ctx = replay_result.get("_ctx")
    if ctx is None:
        return None
    geometry = _extract_candidate_geometry(replay_result, strategy_id)
    if geometry is None or geometry["entry"] is None or geometry["stop_loss"] is None or geometry["take_profit"] is None:
        return None

    quote = replay_result.get("_quote")
    real_spread = float(quote.spread) if (replay_result.get("source") == "SNAPSHOT" and quote is not None) else None
    entry_time = datetime.fromisoformat(replay_result["as_of"])
    entry_time = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)

    from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION

    fields = build_fingerprint(
        ctx=ctx, strategy_id=strategy_id, contributing_strategies=list(contributing), strategy_family=strategy_family,
        strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier=replay_result["source"], provider=provider, proxy=False,
        entry=float(geometry["entry"]), stop_loss=float(geometry["stop_loss"]), take_profit=float(geometry["take_profit"]),
        entry_time=entry_time, confidence_band=confidence_band, real_spread=real_spread,
    )

    fingerprint_id = _fingerprint_id(evaluation_id, strategy_id)
    with SessionLocal() as db:
        row = db.get(HistoricalPatternFingerprintORM, fingerprint_id)
        if row is None:
            row = HistoricalPatternFingerprintORM(fingerprint_id=fingerprint_id, source_evaluation_id=evaluation_id, created_at=datetime.now(timezone.utc))
            db.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()
        persisted = {column.name: getattr(row, column.name) for column in HistoricalPatternFingerprintORM.__table__.columns}

    outcome_row = None
    if label_outcome:
        outcome_row = outcomes.label_outcome(
            fingerprint_id=fingerprint_id, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol,
            direction=fields["direction"], entry=fields["entry"], stop_loss=fields["stop_loss"], take_profit=fields["take_profit"],
            entry_time=fields["entry_time"], provider=provider, real_spread=real_spread,
        )

    return {"fingerprint": persisted, "outcome": outcome_row}


async def build_fingerprints_for_evaluations(evaluation_ids: list[str], *, provider: str = "MT5") -> dict[str, Any]:
    """Bulk driver over an explicit list of REAL evaluation_ids (caller-selected, e.g. from a
    real DB query -- this function never invents which evaluations to process)."""
    built = 0
    skipped = 0
    errors = 0
    for evaluation_id in evaluation_ids:
        try:
            result = await build_fingerprint_for_evaluation(evaluation_id, provider=provider)
        except Exception:
            errors += 1
            continue
        if result is None:
            skipped += 1
        else:
            built += 1
    return {"total_requested": len(evaluation_ids), "built": built, "skipped": skipped, "errors": errors}

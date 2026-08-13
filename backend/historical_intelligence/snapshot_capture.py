"""Decision-time snapshot capture (Option B) -- the authoritative parity-verification source.

Called from backend/brokers/mt5/autonomous.py::MT5AutonomousTradingService._record_cycle, in the
SAME best-effort, try/except-isolated, AFTER-the-decision-is-already-made style as the existing
capture_cycle_candidate_evaluations() call it sits alongside. A failure here can NEVER affect a
live trading decision -- it only ever runs after `result` (the cycle's already-finalized outcome)
exists, and the caller wraps this in its own try/except.

Reads self._cycle_context_cache (backend/brokers/mt5/autonomous.py's own in-memory, per-cycle
StrategyContext cache, already populated by _screen() for every symbol that underwent full
multi-strategy analysis this cycle) -- never re-fetches from the broker, never adds a network
call to the live cycle.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence.orm import MT5DecisionSnapshotORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_id(evaluation_id: str) -> str:
    return "MDS_" + hashlib.sha256(evaluation_id.encode()).hexdigest()[:40]


def capture_decision_snapshots(service: Any, result: dict[str, Any]) -> int:
    """Persists one MT5DecisionSnapshotORM row per candidate in `result["candidates"]` whose
    symbol has a cached StrategyContext on `service` from THIS cycle. Idempotent per
    evaluation_id (matches capture_cycle_candidate_evaluations' own id derivation exactly, so
    the two tables are joinable) -- a row that already exists is left untouched. Returns the
    number of new rows written."""
    cycle_id = result.get("cycle_id")
    if not cycle_id:
        return 0
    account_id = str(result.get("account_id") or getattr(service, "account_id", "demo_10k"))
    context_cache = getattr(service, "_cycle_context_cache", None) or {}
    if not context_cache:
        return 0
    candidates = [c for c in (result.get("candidates") or []) if isinstance(c, dict) and "trade_confidence" in c]
    if not candidates:
        return 0

    written = 0
    now = utcnow()
    with SessionLocal() as db:
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            broker_symbol = str(candidate.get("broker_symbol") or "").upper()
            ctx = context_cache.get(broker_symbol)
            if not candidate_id or ctx is None:
                continue
            # Matches candidate_evaluation.py::capture_cycle_candidate_evaluations' own
            # evaluation_id derivation EXACTLY (_hash({"candidate_id": candidate_id})[:32], where
            # _hash = sha256(json.dumps(payload, sort_keys=True, default=str))) so the two tables
            # share the same evaluation_id and are directly joinable.
            evaluation_id = hashlib.sha256(json.dumps({"candidate_id": candidate_id}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]
            snapshot_id = _snapshot_id(evaluation_id)
            if db.get(MT5DecisionSnapshotORM, snapshot_id) is not None:
                continue

            symbol = str(candidate.get("canonical_pair") or candidate.get("symbol") or "").upper()
            try:
                row = MT5DecisionSnapshotORM(
                    snapshot_id=snapshot_id, evaluation_id=evaluation_id, cycle_id=str(cycle_id), account_id=account_id,
                    canonical_symbol=symbol, broker_symbol=broker_symbol, decision_at=now,
                    m15_rows=ctx.m15_rows, h1_rows=ctx.h1_rows, h4_rows=ctx.h4_rows,
                    bid=float(ctx.bid) if ctx.bid is not None else None, ask=float(ctx.ask) if ctx.ask is not None else None, spread=float(ctx.spread) if ctx.spread is not None else None,
                    regime=ctx.regime, smc_evidence={}, symbol_info={}, created_at=now,
                )
                db.add(row)
                db.flush()
                written += 1
            except Exception as exc:
                logger.warning("Decision snapshot capture failed for candidate_id=%s: %s", candidate_id, exc.__class__.__name__)
                db.rollback()
                continue
        db.commit()
    return written

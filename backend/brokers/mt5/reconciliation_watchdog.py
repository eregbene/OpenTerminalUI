"""Broker/internal state reconciliation watchdog (Phase 3 of the Forex/MT5 roadmap).

Read-only comparison between real MT5 broker-reported open positions and Postgres's own
AdaptivePositionStateORM, per account. Complements -- never replaces -- the existing sync logic
in backend/adaptive_management/service.py: `_sync_position_state` (broker-truth push into DB on
every cycle) and `_reconcile_recently_closed` (a DB row with no matching broker position is
marked closed). Confirmed by direct audit that NEITHER of those (a) records an explicit,
queryable finding when SL/TP/volume disagree -- they simply overwrite silently -- or (b) flags a
broker position that has NO matching DB row at all as anything unusual (it's silently treated as
brand new). This module adds exactly those two things, without changing either existing
mechanism's behavior.

Protective response is DELIBERATELY conservative (explicit instruction: do not auto-close a
position purely because a discrepancy exists). This module only:
  1. persists every discrepancy found as an explicit, timestamped finding (never silently drops
     one), and
  2. marks an account's state UNTRUSTED_STATE when discrepancies exceed a small tolerance,
     which `is_account_state_trustworthy()` exposes for autonomous.py to check before allowing
     NEW entries for that account -- existing positions continue to be managed by the Adaptive
     Manager exactly as before (it reads broker state directly on every cycle, independent of
     this watchdog's own findings).

Account isolation: every comparison is scoped to exactly one account_id at a time via
multi_account.adapter_for_account (already `read_only=True` by construction) -- one account's
bridge being unreachable or its state being untrustworthy never touches the other three.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.multi_account import adapter_for_account
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

_PRICE_TOLERANCE = 0.00005  # absolute price-unit tolerance -- absorbs broker-side floating-point
# rounding on SL/TP echoes, never a real discrepancy this small.
_VOLUME_TOLERANCE = 0.001  # lots

DB_POSITION_MISSING_ON_BROKER = "DB_POSITION_MISSING_ON_BROKER"
BROKER_POSITION_MISSING_IN_DB = "BROKER_POSITION_MISSING_IN_DB"
SL_MISMATCH = "SL_MISMATCH"
TP_MISMATCH = "TP_MISMATCH"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
UNKNOWN_BROKER_POSITION = "UNKNOWN_BROKER_POSITION"

# An account with any finding in this set cannot be trusted for new entries until the NEXT clean
# reconciliation pass. UNKNOWN_BROKER_POSITION is deliberately excluded -- a manually-placed
# position that isn't ours is informational, not evidence Bensim's own state is wrong.
_UNTRUSTING_FINDING_TYPES = {DB_POSITION_MISSING_ON_BROKER, BROKER_POSITION_MISSING_IN_DB, SL_MISMATCH, TP_MISMATCH, VOLUME_MISMATCH}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _position_id(payload: dict[str, Any], account_id: str) -> str:
    """Matches backend/adaptive_management/service.py::_position_id exactly -- the SAME id
    scheme, reused (not reimplemented), so this module's lookups actually hit the real rows."""
    raw = str(payload.get("identifier") or payload.get("ticket") or hashlib.sha256(str(payload).encode()).hexdigest()[:32])
    return raw if account_id == "demo_10k" else f"{account_id}:{raw}"


def _is_close(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


async def reconcile_account(account_id: str) -> dict[str, Any]:
    """Runs one reconciliation pass for a single account. Returns a summary dict and PERSISTS
    every finding + an account-level status row (upserted, one row per account_id -- see
    is_account_state_trustworthy). Never raises -- a broker-fetch failure is itself reported as
    an untrusted-state finding (state cannot be verified => cannot be trusted), never silently
    swallowed."""
    from backend.brokers.mt5.orm import MT5AccountReconciliationFindingORM, MT5AccountReconciliationStatusORM

    findings: list[dict[str, Any]] = []
    try:
        adapter = adapter_for_account(account_id)
        broker_positions = await adapter.mt5_positions()
        config = adapter.config
    except Exception as exc:
        logger.warning("Reconciliation: broker fetch failed for account_id=%s: %s", account_id, exc.__class__.__name__)
        findings.append({"finding_type": DB_POSITION_MISSING_ON_BROKER, "position_id": None, "detail": f"broker fetch failed: {exc.__class__.__name__}"})
        _persist(account_id=account_id, findings=findings, trustworthy=False)
        return {"account_id": account_id, "trustworthy": False, "findings": findings, "reason": "BROKER_UNREACHABLE"}

    owned_broker = [p for p in broker_positions if p.magic == config.bensim_magic or str(p.comment or "").startswith(("BENSIM_AUTO", "BSM|"))]
    foreign_broker = [p for p in broker_positions if p not in owned_broker]
    broker_by_id = {_position_id(p.model_dump(mode="json"), account_id): p for p in owned_broker}

    with SessionLocal() as db:
        db_rows = db.query(AdaptivePositionStateORM).filter(AdaptivePositionStateORM.account_id == account_id, AdaptivePositionStateORM.closed_detected_at.is_(None)).all()
    db_by_id = {row.position_id: row for row in db_rows}

    for position_id, row in db_by_id.items():
        if position_id not in broker_by_id:
            findings.append({"finding_type": DB_POSITION_MISSING_ON_BROKER, "position_id": position_id, "detail": f"DB has an open position (ticket={row.broker_ticket}) the broker no longer reports"})

    for position_id, pos in broker_by_id.items():
        row = db_by_id.get(position_id)
        if row is None:
            findings.append({"finding_type": BROKER_POSITION_MISSING_IN_DB, "position_id": position_id, "detail": f"broker reports an open Bensim position (ticket={pos.ticket}) with no matching DB row"})
            continue
        if not _is_close(float(pos.sl) if pos.sl else None, row.current_sl, _PRICE_TOLERANCE):
            findings.append({"finding_type": SL_MISMATCH, "position_id": position_id, "detail": f"broker sl={pos.sl} db sl={row.current_sl}"})
        if not _is_close(float(pos.tp) if pos.tp else None, row.current_tp, _PRICE_TOLERANCE):
            findings.append({"finding_type": TP_MISMATCH, "position_id": position_id, "detail": f"broker tp={pos.tp} db tp={row.current_tp}"})
        if not _is_close(float(pos.volume), row.current_volume, _VOLUME_TOLERANCE):
            findings.append({"finding_type": VOLUME_MISMATCH, "position_id": position_id, "detail": f"broker volume={pos.volume} db volume={row.current_volume}"})

    for pos in foreign_broker:
        findings.append({"finding_type": UNKNOWN_BROKER_POSITION, "position_id": str(pos.ticket), "detail": f"non-Bensim position on this account (magic={pos.magic}, comment={pos.comment!r})"})

    trustworthy = not any(f["finding_type"] in _UNTRUSTING_FINDING_TYPES for f in findings)
    _persist(account_id=account_id, findings=findings, trustworthy=trustworthy)
    return {"account_id": account_id, "trustworthy": trustworthy, "findings": findings, "broker_position_count": len(owned_broker), "db_position_count": len(db_by_id)}


def _persist(*, account_id: str, findings: list[dict[str, Any]], trustworthy: bool) -> None:
    from backend.brokers.mt5.orm import MT5AccountReconciliationFindingORM, MT5AccountReconciliationStatusORM

    now = utcnow()
    with SessionLocal() as db:
        status_row = db.get(MT5AccountReconciliationStatusORM, account_id)
        if status_row is None:
            status_row = MT5AccountReconciliationStatusORM(account_id=account_id)
            db.add(status_row)
        status_row.trustworthy = trustworthy
        status_row.finding_count = len(findings)
        status_row.last_checked_at = now
        for finding in findings:
            finding_id = "MRF_" + hashlib.sha256(f"{account_id}:{finding['finding_type']}:{finding['position_id']}:{now.isoformat()}".encode()).hexdigest()[:40]
            db.add(MT5AccountReconciliationFindingORM(
                finding_id=finding_id, account_id=account_id, finding_type=finding["finding_type"],
                position_id=finding["position_id"], detail=finding["detail"], created_at=now,
            ))
        db.commit()


def is_account_state_trustworthy(account_id: str) -> bool:
    """Read-only check for autonomous.py's live entry path -- does NOT run a fresh reconciliation
    (that's a separate, scheduled/on-demand pass, never inline in the M5 cycle). Fails CLOSED
    (returns False, i.e. block new entries) when no reconciliation has ever run for this account
    -- an account whose state has never been verified is not assumed trustworthy by default."""
    from backend.brokers.mt5.orm import MT5AccountReconciliationStatusORM

    with SessionLocal() as db:
        row = db.get(MT5AccountReconciliationStatusORM, account_id)
    if row is None:
        return False
    return bool(row.trustworthy)


async def reconcile_all_accounts(account_ids: list[str]) -> dict[str, dict[str, Any]]:
    results = {}
    for account_id in account_ids:
        try:
            results[account_id] = await reconcile_account(account_id)
        except Exception as exc:
            logger.warning("Reconciliation pass failed entirely for account_id=%s: %s", account_id, exc.__class__.__name__)
            results[account_id] = {"account_id": account_id, "trustworthy": False, "findings": [], "reason": f"RECONCILIATION_ERROR:{exc.__class__.__name__}"}
    return results

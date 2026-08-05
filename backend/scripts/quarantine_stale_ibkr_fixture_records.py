from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, func, select

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.brokers.ibkr.orm import BrokerContractORM, BrokerEventORM, BrokerExecutionORM, BrokerOrderORM, BrokerReconciliationORM
from backend.shared.db import SessionLocal


def stable_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


FIXTURE_SOURCES = {"FIXTURE_IBKR", "LOCAL_SIMULATOR", "TEST_SEED"}
REAL_SOURCES = {"REAL_IBKR", "REAL_IBKR_PAPER"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_order(order: BrokerOrderORM, contract: BrokerContractORM | None) -> str:
    evidence = " ".join(
        str(value or "")
        for value in (
            order.id,
            order.order_reference,
            order.client_order_id,
            order.broker_order_id,
            order.permanent_id,
            order.candidate_id,
            order.oms_intent_id,
            order.record_source,
            contract.id if contract else "",
            contract.record_source if contract else "",
            contract.verification_source if contract else "",
            contract.con_id if contract else "",
        )
    ).lower()
    if order.record_source in REAL_SOURCES or (contract and contract.record_source in REAL_SOURCES and contract.con_id not in {0, 123}):
        return "REAL_IBKR"
    if order.record_source in FIXTURE_SOURCES or "test_" in evidence or "fx5b" in evidence or "fixture" in evidence or "simulator" in evidence:
        return "FIXTURE_IBKR"
    return "UNKNOWN_LEGACY"


def reject_wildcard(value: str) -> None:
    if not value or any(char in value for char in "*?%"):
        raise SystemExit("QUARANTINE_BLOCKED: exact --order-ref is required; wildcards are not allowed")


def audit_payload(order: BrokerOrderORM, contract: BrokerContractORM | None, classification: str, counts: dict[str, int], reason: str) -> dict[str, Any]:
    return {
        "target_order_id": order.id,
        "target_order_reference": order.order_reference,
        "classification": classification,
        "masked_account_id": order.masked_account_id,
        "canonical_symbol": order.canonical_symbol,
        "contract_id": order.contract_id,
        "contract_source": contract.record_source if contract else None,
        "reason": reason,
        "affected_counts": counts,
    }


def run(order_ref: str, *, apply: bool, reason: str, actor: str = "codex-admin") -> dict[str, Any]:
    reject_wildcard(order_ref)
    now = utcnow()
    with SessionLocal() as session:
        order = session.scalars(select(BrokerOrderORM).where((BrokerOrderORM.id == order_ref) | (BrokerOrderORM.order_reference == order_ref))).first()
        if not order:
            raise SystemExit("QUARANTINE_BLOCKED: target order not found")
        contract = session.get(BrokerContractORM, order.contract_id) if order.contract_id else None
        classification = classify_order(order, contract)
        event_count = session.scalar(select(func.count()).select_from(BrokerEventORM).where(BrokerEventORM.broker_order_id == order.id)) or 0
        execution_count = session.scalar(select(func.count()).select_from(BrokerExecutionORM).where(BrokerExecutionORM.broker_order_id == order.id)) or 0
        reconciliation_count = session.scalar(select(func.count()).select_from(BrokerReconciliationORM).where((BrokerReconciliationORM.order_id == order.id) | (cast(BrokerReconciliationORM.differences, String).like(f"%{order.id}%")))) or 0
        counts = {
            "broker_orders": 1,
            "broker_events": int(event_count),
            "broker_executions": int(execution_count),
            "broker_reconciliations": int(reconciliation_count),
            "broker_contracts": 1 if contract else 0,
        }
        blocking_reasons: list[str] = []
        if classification == "REAL_IBKR":
            blocking_reasons.append("REAL_RECORD_REJECTED")
        if order.record_source == "REAL_IBKR":
            blocking_reasons.append("ORDER_SOURCE_REAL_IBKR")
        if contract and contract.record_source == "REAL_IBKR" and contract.con_id not in {0, 123}:
            blocking_reasons.append("CONTRACT_SOURCE_REAL_IBKR")
        if execution_count:
            blocking_reasons.append("ACTUAL_EXECUTION_PRESENT")
        eligible = not blocking_reasons and classification in {"FIXTURE_IBKR", "UNKNOWN_LEGACY"}
        payload = {
            "mode": "apply" if apply else "dry-run",
            "target": audit_payload(order, contract, classification, counts, reason),
            "eligible": eligible,
            "blocking_reasons": blocking_reasons,
            "already_quarantined": bool(order.is_quarantined),
        }
        if not apply:
            session.rollback()
            return payload
        if not eligible:
            session.rollback()
            raise SystemExit("QUARANTINE_BLOCKED: " + ",".join(blocking_reasons or ["NOT_ELIGIBLE"]))

        order.record_source = "FIXTURE_IBKR" if classification == "FIXTURE_IBKR" else "UNKNOWN_LEGACY"
        order.is_quarantined = True
        order.quarantined_at = order.quarantined_at or now
        order.quarantine_reason = order.quarantine_reason or reason
        order.test_run_id = order.test_run_id or "fx5b"
        if contract and classification == "FIXTURE_IBKR":
            contract.record_source = "FIXTURE_IBKR"
            contract.is_quarantined = True
            contract.quarantined_at = contract.quarantined_at or now
            contract.quarantine_reason = contract.quarantine_reason or reason
            contract.test_run_id = contract.test_run_id or "fx5b"
            contract.verification_source = "FIXTURE"
            contract.verified = False
        events = session.scalars(select(BrokerEventORM).where(BrokerEventORM.broker_order_id == order.id)).all()
        for event in events:
            event.record_source = order.record_source
            event.is_quarantined = True
            event.quarantined_at = event.quarantined_at or now
            event.quarantine_reason = event.quarantine_reason or reason
            event.test_run_id = event.test_run_id or order.test_run_id
        executions = session.scalars(select(BrokerExecutionORM).where(BrokerExecutionORM.broker_order_id == order.id)).all()
        for execution in executions:
            execution.record_source = order.record_source
            execution.is_quarantined = True
            execution.quarantined_at = execution.quarantined_at or now
            execution.quarantine_reason = execution.quarantine_reason or reason
            execution.test_run_id = execution.test_run_id or order.test_run_id

        existing_audit = session.scalars(select(BrokerEventORM).where(BrokerEventORM.broker_order_id == order.id, BrokerEventORM.event_type == "FIXTURE_RECORD_QUARANTINED")).first()
        if not existing_audit:
            next_sequence = int(session.scalar(select(func.coalesce(func.max(BrokerEventORM.sequence), 0)).where(BrokerEventORM.broker_order_id == order.id)) or 0) + 1
            event_payload = {**payload["target"], "actor": actor, "quarantined_at": now.isoformat()}
            audit_id = f"ibkrquarantine_{stable_hash([order.id, next_sequence, reason])[:16]}"
            session.add(
                BrokerEventORM(
                    id=audit_id,
                    broker_order_id=order.id,
                    event_type="FIXTURE_RECORD_QUARANTINED",
                    received_at=now,
                    sequence=next_sequence,
                    payload=event_payload,
                    payload_hash=stable_hash(event_payload),
                    record_source=order.record_source,
                    is_quarantined=True,
                    quarantined_at=now,
                    quarantine_reason=reason,
                    test_run_id=order.test_run_id,
                    created_at=now,
                )
            )
            counts["broker_events"] += 1
        session.commit()
        payload["applied"] = True
        payload["target"]["affected_counts"] = counts
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantine exact stale IBKR fixture lineage without deleting audit history.")
    parser.add_argument("--order-ref", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reason", default="STALE_FX5B_TEST_FIXTURE")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Choose either --dry-run or --apply, not both")
    payload = run(args.order_ref, apply=bool(args.apply), reason=args.reason)
    print(json.dumps(payload, default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

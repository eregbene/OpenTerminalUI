from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.exc import IntegrityError

from backend.brokers.ibkr.orm import (
    BrokerConnectionSessionORM,
    BrokerContractORM,
    BrokerEventORM,
    BrokerExecutionORM,
    BrokerIncidentORM,
    BrokerOrderORM,
    BrokerReconciliationORM,
    BrokerRecoveryRunORM,
)
from backend.shared.db import SessionLocal, engine
from backend.trading.serialization import model_to_jsonable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def broker_tables_available() -> bool:
    inspector = inspect(engine)
    return all(
        inspector.has_table(name)
        for name in {
            "broker_connection_sessions",
            "broker_contracts",
            "broker_orders",
            "broker_events",
            "broker_executions",
            "broker_reconciliations",
            "broker_recovery_runs",
            "broker_incidents",
        }
    )


def _dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _record_source(value: str | None, *, fallback: str = "UNKNOWN_LEGACY") -> str:
    normalized = (value or fallback).strip().upper()
    allowed = {"REAL_IBKR", "FIXTURE_IBKR", "LOCAL_SIMULATOR", "TEST_SEED", "UNKNOWN_LEGACY"}
    return normalized if normalized in allowed else fallback


def _blank_to_none(value: Any) -> Any | None:
    return None if value in {None, ""} else value


class IbkrAcceptanceDbStore:
    source = "POSTGRESQL"

    def load(self) -> dict[str, Any]:
        with SessionLocal() as session:
            account_rows = session.scalars(select(BrokerConnectionSessionORM)).all()
            contract_rows = session.scalars(select(BrokerContractORM)).all()
            order_rows = session.scalars(select(BrokerOrderORM)).all()
            event_rows = session.scalars(select(BrokerEventORM).order_by(BrokerEventORM.received_at, BrokerEventORM.sequence)).all()
            recon_rows = session.scalars(select(BrokerReconciliationORM).order_by(BrokerReconciliationORM.created_at)).all()
            incident_rows = session.scalars(select(BrokerIncidentORM).order_by(BrokerIncidentORM.created_at)).all()
            recovery = session.scalars(select(BrokerRecoveryRunORM).order_by(BrokerRecoveryRunORM.created_at.desc())).first()
        return {
            "account_verifications": {row.id: self._verification(row) for row in account_rows},
            "contracts": {row.canonical_symbol: self._contract(row) for row in contract_rows},
            "broker_orders": {row.id: self._order(row) for row in order_rows},
            "events": [self._event(row) for row in event_rows],
            "executions": [self._execution(row) for row in self.executions()],
            "reconciliations": [self._reconciliation(row) for row in recon_rows],
            "incidents": [self._incident(row) for row in incident_rows],
            "recovery": self._recovery(recovery),
        }

    def save(self, data: dict[str, Any]) -> None:
        _ = data
        raise RuntimeError("IBKR_DB_STORE_IS_STRUCTURED")

    def upsert_verification(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        now = utcnow()
        with SessionLocal() as session:
            existing = session.get(BrokerConnectionSessionORM, payload["verification_id"])
            values = {
                "id": payload["verification_id"],
                "broker": "IBKR",
                "environment": payload.get("trading_environment") or "PAPER",
                "session_id": payload["session_id"],
                "host": payload.get("server_host"),
                "port": payload.get("server_port"),
                "client_id": payload.get("client_id"),
                "masked_account_id": payload.get("account_id_masked"),
                "account_id_hash": payload.get("account_id_hash"),
                "connection_state": "ACCOUNT_VERIFIED" if payload.get("accepted") else "BLOCKED",
                "account_verified": bool(payload.get("accepted")),
                "market_data_mode": payload.get("market_data_mode") or "UNKNOWN",
                "connected_at": _dt(payload.get("connected_at")),
                "verified_at": _dt(payload.get("verified_at")),
                "last_heartbeat_at": now,
                "last_server_time": _dt(payload.get("last_server_time")) or now,
                "last_error_code": (payload.get("rejection_reasons") or [None])[0],
                "last_error_message": "|".join(payload.get("rejection_reasons") or []),
                "updated_at": now,
            }
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                values["created_at"] = now
                session.add(BrokerConnectionSessionORM(**values))
            session.commit()

    def upsert_contract(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        now = utcnow()
        values = {
            "id": payload["contract_id"],
            "broker": payload.get("broker", "IBKR"),
            "environment": payload.get("environment", "PAPER"),
            "canonical_symbol": payload["canonical_symbol"],
            "con_id": int(payload.get("con_id") or 0),
            "security_type": payload.get("security_type") or "UNKNOWN",
            "exchange": payload.get("exchange") or "UNKNOWN",
            "currency": payload.get("currency") or payload.get("quote_currency") or "USD",
            "local_symbol": payload.get("local_symbol"),
            "trading_class": payload.get("trading_class"),
            "minimum_tick": _dec(payload.get("minimum_tick")),
            "quantity_increment": _dec((payload.get("quantity_rules") or {}).get("minimum_quantity", "1")),
            "market_rule_ids": payload.get("market_rule_ids") or [],
            "contract_payload": payload,
            "verified": bool(payload.get("verified")),
            "verified_at": _dt(payload.get("contract_details_received_at")) if payload.get("verified") else None,
            "verification_source": payload.get("verification_source") or "FIXTURE",
            "record_source": _record_source(
                payload.get("record_source"),
                fallback="REAL_IBKR" if payload.get("verification_source") in {"REAL_IBKR", "REAL_IBKR_PAPER"} else "FIXTURE_IBKR",
            ),
            "is_quarantined": bool(payload.get("is_quarantined", False)),
            "quarantined_at": _dt(payload.get("quarantined_at")),
            "quarantine_reason": payload.get("quarantine_reason"),
            "test_run_id": payload.get("test_run_id"),
            "content_hash": payload.get("content_hash") or "",
            "updated_at": now,
        }
        with SessionLocal() as session:
            existing = session.get(BrokerContractORM, values["id"])
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                values["created_at"] = now
                session.add(BrokerContractORM(**values))
            session.commit()

    def upsert_order(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        now = utcnow()
        values = {
            "id": payload["broker_order_record_id"],
            "candidate_id": payload.get("candidate_id"),
            "risk_decision_id": payload.get("risk_decision_id"),
            "oms_intent_id": payload.get("oms_intent_id"),
            "broker": payload.get("broker", "IBKR"),
            "environment": payload.get("environment", "PAPER"),
            "masked_account_id": payload.get("account_id_masked"),
            "canonical_symbol": payload.get("canonical_symbol"),
            "contract_id": payload.get("contract_id"),
            "client_order_id": payload.get("client_order_id"),
            "broker_order_id": _blank_to_none(payload.get("broker_order_id")),
            "permanent_id": _blank_to_none(payload.get("permanent_id")),
            "parent_order_id": payload.get("parent_order_id"),
            "order_reference": payload.get("order_reference"),
            "side": payload.get("side"),
            "order_type": payload.get("order_type"),
            "quantity": _dec(payload.get("quantity")) or Decimal("0"),
            "limit_price": _dec(payload.get("limit_price")),
            "stop_price": _dec(payload.get("stop_price")),
            "time_in_force": payload.get("time_in_force") or "DAY",
            "internal_status": payload.get("internal_status") or "CREATED",
            "broker_status": payload.get("broker_status") or "NOT_SUBMITTED",
            "filled_quantity": _dec(payload.get("filled_quantity")) or Decimal("0"),
            "remaining_quantity": _dec(payload.get("remaining_quantity")) or Decimal("0"),
            "average_fill_price": _dec(payload.get("average_fill_price")),
            "last_fill_price": _dec(payload.get("last_fill_price")),
            "commission": _dec(payload.get("commission")),
            "commission_currency": payload.get("commission_currency") or payload.get("currency"),
            "record_source": _record_source(payload.get("record_source"), fallback=payload.get("execution_provider") if payload.get("execution_provider") in {"REAL_IBKR", "FIXTURE_IBKR", "LOCAL_SIMULATOR", "TEST_SEED"} else "UNKNOWN_LEGACY"),
            "is_quarantined": bool(payload.get("is_quarantined", False)),
            "quarantined_at": _dt(payload.get("quarantined_at")),
            "quarantine_reason": payload.get("quarantine_reason"),
            "test_run_id": payload.get("test_run_id"),
            "broker_session_id": payload.get("broker_session_id"),
            "submitted_at": _dt(payload.get("submitted_at")),
            "acknowledged_at": _dt(payload.get("acknowledged_at")),
            "failure_code": payload.get("failure_code"),
            "failure_message": payload.get("failure_message"),
            "content_hash": payload.get("content_hash") or "",
            "updated_at": now,
        }
        with SessionLocal() as session:
            existing = session.get(BrokerOrderORM, values["id"])
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                values["created_at"] = now
                session.add(BrokerOrderORM(**values))
            session.commit()

    def append_event(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        with SessionLocal() as session:
            event = BrokerEventORM(
                id=payload["event_id"],
                broker_order_id=payload["broker_order_record_id"],
                event_type=payload["event_type"],
                broker_timestamp=_dt(payload.get("broker_timestamp")),
                received_at=_dt(payload.get("received_at")) or utcnow(),
                sequence=int(payload["sequence"]),
                payload=payload.get("payload") or {},
                payload_hash=payload.get("payload_hash") or "",
                record_source=_record_source(payload.get("record_source")),
                is_quarantined=bool(payload.get("is_quarantined", False)),
                quarantined_at=_dt(payload.get("quarantined_at")),
                quarantine_reason=payload.get("quarantine_reason"),
                test_run_id=payload.get("test_run_id"),
                created_at=utcnow(),
            )
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

    def append_reconciliation(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        now = utcnow()
        with SessionLocal() as session:
            session.add(
                BrokerReconciliationORM(
                    id=payload["reconciliation_id"],
                    broker=payload.get("broker", "IBKR"),
                    environment=payload.get("environment", "PAPER"),
                    session_id=payload.get("session_id"),
                    trade_id=payload.get("trade_id"),
                    order_id=payload.get("order_id"),
                    status=payload.get("status", "UNKNOWN"),
                    local_snapshot=payload.get("local_snapshot") or {},
                    broker_snapshot=payload.get("broker_snapshot") or {},
                    differences=payload.get("mismatches") or payload.get("differences") or [],
                    scope_metadata=payload.get("scope_metadata") or {},
                    blocking=bool(payload.get("blocking")),
                    started_at=_dt(payload.get("started_at")) or now,
                    completed_at=_dt(payload.get("completed_at")) or now,
                    created_at=now,
                )
            )
            session.commit()

    def append_incident(self, row: Any) -> None:
        payload = model_to_jsonable(row)
        now = utcnow()
        with SessionLocal() as session:
            session.add(
                BrokerIncidentORM(
                    id=payload["incident_id"],
                    broker=payload.get("broker", "IBKR"),
                    environment=payload.get("environment", "PAPER"),
                    severity=payload.get("severity", "INFO"),
                    incident_type=payload.get("code") or payload.get("incident_type") or "UNKNOWN",
                    status=payload.get("status") or "OPEN",
                    order_id=payload.get("order_id"),
                    trade_id=payload.get("trade_id"),
                    message=payload.get("message", ""),
                    details=payload.get("details") or {},
                    blocking=bool(payload.get("blocking")),
                    opened_at=_dt(payload.get("opened_at")) or _dt(payload.get("created_at")) or now,
                    resolved_at=_dt(payload.get("resolved_at")),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

    def set_recovery(self, status: str, blocking: bool) -> None:
        now = utcnow()
        run_id = f"ibkrrecovery_{now.strftime('%Y%m%d%H%M%S%f')}"
        with SessionLocal() as session:
            if status == "RECOVERY_IN_PROGRESS":
                session.add(
                    BrokerRecoveryRunORM(
                        id=run_id,
                        broker="IBKR",
                        environment="PAPER",
                        status=status,
                        started_at=now,
                        completed_at=None,
                        blocking_reason="RECOVERY_IN_PROGRESS" if blocking else None,
                        details={"blocking": blocking},
                        created_at=now,
                    )
                )
            else:
                latest = session.scalars(select(BrokerRecoveryRunORM).order_by(BrokerRecoveryRunORM.created_at.desc())).first()
                if latest and latest.status == "RECOVERY_IN_PROGRESS":
                    latest.status = status
                    latest.completed_at = now
                    latest.blocking_reason = "RECONCILIATION_BLOCKING" if blocking else None
                    latest.details = {**(latest.details or {}), "blocking": blocking}
                else:
                    session.add(
                        BrokerRecoveryRunORM(
                            id=run_id,
                            broker="IBKR",
                            environment="PAPER",
                            status=status,
                            started_at=now,
                            completed_at=now,
                            blocking_reason="RECONCILIATION_BLOCKING" if blocking else None,
                            details={"blocking": blocking},
                            created_at=now,
                        )
                    )
            session.commit()

    def executions(self, order_id: str | None = None) -> list[BrokerExecutionORM]:
        with SessionLocal() as session:
            stmt = select(BrokerExecutionORM).order_by(BrokerExecutionORM.execution_time)
            if order_id:
                stmt = stmt.where(BrokerExecutionORM.broker_order_id == order_id)
            return list(session.scalars(stmt).all())

    def _verification(self, row: BrokerConnectionSessionORM) -> dict[str, Any]:
        return {
            "verification_id": row.id,
            "broker": row.broker,
            "account_id_masked": row.masked_account_id,
            "account_alias": None,
            "account_type": "PAPER" if row.environment == "PAPER" else "UNKNOWN",
            "account_mode": row.environment,
            "trading_environment": row.environment,
            "server_host": row.host,
            "server_port": row.port,
            "client_id": row.client_id,
            "session_id": row.session_id,
            "connected_at": row.connected_at,
            "verified_at": row.verified_at,
            "verification_source": ["postgresql", "broker_accounts", "allowlist"],
            "live_execution_allowed": False,
            "accepted": row.account_verified,
            "rejection_reasons": [row.last_error_code] if row.last_error_code and not row.account_verified else [],
        }

    def _contract(self, row: BrokerContractORM) -> dict[str, Any]:
        payload = dict(row.contract_payload or {})
        payload.update(
            {
                "broker": row.broker,
                "environment": row.environment,
                "canonical_symbol": row.canonical_symbol,
                "contract_id": row.id,
                "security_type": row.security_type,
                "exchange": row.exchange,
                "currency": row.currency,
                "local_symbol": row.local_symbol,
                "con_id": row.con_id,
                "trading_class": row.trading_class,
                "minimum_tick": str(row.minimum_tick) if row.minimum_tick is not None else "0",
                "quantity_rules": {"type": "units", "minimum_quantity": str(row.quantity_increment or 1), "whole_units": True},
                "market_rule_ids": row.market_rule_ids or [],
                "verified": row.verified,
                "verification_source": row.verification_source,
                "record_source": row.record_source,
                "is_quarantined": row.is_quarantined,
                "quarantined_at": row.quarantined_at,
                "quarantine_reason": row.quarantine_reason,
                "test_run_id": row.test_run_id,
                "usable_for_real_submission": row.verification_source == "REAL_IBKR_PAPER",
                "content_hash": row.content_hash,
            }
        )
        return payload

    def _order(self, row: BrokerOrderORM) -> dict[str, Any]:
        return {
            "broker_order_record_id": row.id,
            "broker": row.broker,
            "environment": row.environment,
            "account_id_masked": row.masked_account_id,
            "candidate_id": row.candidate_id,
            "risk_decision_id": row.risk_decision_id,
            "oms_intent_id": row.oms_intent_id,
            "canonical_symbol": row.canonical_symbol,
            "contract_id": row.contract_id,
            "client_order_id": row.client_order_id,
            "broker_order_id": row.broker_order_id,
            "permanent_id": row.permanent_id,
            "parent_order_id": row.parent_order_id,
            "order_reference": row.order_reference,
            "order_type": row.order_type,
            "side": row.side,
            "quantity": str(row.quantity),
            "limit_price": str(row.limit_price) if row.limit_price is not None else None,
            "stop_price": str(row.stop_price) if row.stop_price is not None else None,
            "time_in_force": row.time_in_force,
            "submitted_at": row.submitted_at,
            "acknowledged_at": row.acknowledged_at,
            "broker_status": row.broker_status,
            "internal_status": row.internal_status,
            "filled_quantity": str(row.filled_quantity),
            "remaining_quantity": str(row.remaining_quantity),
            "average_fill_price": str(row.average_fill_price) if row.average_fill_price is not None else None,
            "last_fill_price": str(row.last_fill_price) if row.last_fill_price is not None else None,
            "commission": str(row.commission) if row.commission is not None else None,
            "commission_status": "RECEIVED" if row.commission is not None else "PENDING",
            "currency": row.commission_currency or "USD",
            "record_source": row.record_source,
            "is_quarantined": row.is_quarantined,
            "quarantined_at": row.quarantined_at,
            "quarantine_reason": row.quarantine_reason,
            "test_run_id": row.test_run_id,
            "broker_session_id": row.broker_session_id,
            "failure_code": row.failure_code,
            "failure_message": row.failure_message,
            "execution_provider": "IBKR_PAPER",
            "content_hash": row.content_hash,
        }

    def _event(self, row: BrokerEventORM) -> dict[str, Any]:
        return {
            "event_id": row.id,
            "broker_order_record_id": row.broker_order_id,
            "event_type": row.event_type,
            "broker_timestamp": row.broker_timestamp,
            "received_at": row.received_at,
            "sequence": row.sequence,
            "payload": row.payload or {},
            "payload_hash": row.payload_hash,
            "record_source": row.record_source,
            "is_quarantined": row.is_quarantined,
            "quarantined_at": row.quarantined_at,
            "quarantine_reason": row.quarantine_reason,
            "test_run_id": row.test_run_id,
        }

    def _execution(self, row: BrokerExecutionORM) -> dict[str, Any]:
        return {
            "id": row.id,
            "broker_order_id": row.broker_order_id,
            "execution_id": row.execution_id,
            "broker_execution_id": row.broker_execution_id,
            "side": row.side,
            "quantity": str(row.quantity),
            "price": str(row.price),
            "currency": row.currency,
            "exchange": row.exchange,
            "execution_time": row.execution_time,
            "commission": str(row.commission) if row.commission is not None else None,
            "commission_currency": row.commission_currency,
            "realized_pnl": str(row.realized_pnl) if row.realized_pnl is not None else None,
            "payload": row.payload or {},
            "content_hash": row.content_hash,
            "record_source": row.record_source,
            "is_quarantined": row.is_quarantined,
            "quarantined_at": row.quarantined_at,
            "quarantine_reason": row.quarantine_reason,
            "test_run_id": row.test_run_id,
        }

    def _reconciliation(self, row: BrokerReconciliationORM) -> dict[str, Any]:
        return {
            "reconciliation_id": row.id,
            "status": row.status,
            "broker": row.broker,
            "environment": row.environment,
            "trade_id": row.trade_id,
            "order_id": row.order_id,
            "mismatches": row.differences or [],
            "scope_metadata": row.scope_metadata or {},
            "blocking": row.blocking,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    def _recovery(self, row: BrokerRecoveryRunORM | None) -> dict[str, Any]:
        if not row:
            return {"status": "IDLE", "last_started_at": None, "last_completed_at": None, "blocking": False}
        return {"status": row.status, "last_started_at": row.started_at, "last_completed_at": row.completed_at, "blocking": bool(row.blocking_reason)}

    def _incident(self, row: BrokerIncidentORM) -> dict[str, Any]:
        return {
            "incident_id": row.id,
            "severity": row.severity,
            "code": row.incident_type,
            "message": row.message,
            "blocking": row.blocking,
            "status": row.status,
            "created_at": row.created_at,
        }

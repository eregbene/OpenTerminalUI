from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerConnectionSessionORM(Base):
    __tablename__ = "broker_connection_sessions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    masked_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    connection_state: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    account_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    market_data_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_server_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BrokerContractORM(Base):
    __tablename__ = "broker_contracts"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    con_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    security_type: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    local_symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trading_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    minimum_tick: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    quantity_increment: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    market_rule_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    contract_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    record_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN_LEGACY", index=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    test_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("broker", "environment", "canonical_symbol", "con_id", name="uq_broker_contract_symbol_conid"),
        Index("ix_broker_contract_lookup", "broker", "environment", "canonical_symbol", "verified"),
    )


class BrokerOrderORM(Base):
    __tablename__ = "broker_orders"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    candidate_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    risk_decision_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    oms_intent_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    masked_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    contract_id: Mapped[str | None] = mapped_column(String(120), ForeignKey("broker_contracts.id"), nullable=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    permanent_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    parent_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    order_reference: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(16), nullable=False, default="DAY")
    internal_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    broker_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    last_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    commission_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    record_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN_LEGACY", index=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    test_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    broker_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("broker", "environment", "order_reference", name="uq_broker_order_reference"),
        UniqueConstraint("broker", "environment", "broker_order_id", name="uq_broker_order_broker_id"),
        UniqueConstraint("broker", "environment", "permanent_id", name="uq_broker_order_permanent_id"),
        Index("ix_broker_order_reconciliation_scope", "broker", "environment", "masked_account_id", "record_source", "is_quarantined"),
    )


class BrokerEventORM(Base):
    __tablename__ = "broker_events"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(120), ForeignKey("broker_orders.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    broker_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    record_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN_LEGACY", index=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    test_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("broker_order_id", "sequence", name="uq_broker_event_order_sequence"),)


class BrokerExecutionORM(Base):
    __tablename__ = "broker_executions"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(120), ForeignKey("broker_orders.id"), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    broker_execution_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USD")
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    commission_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    record_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN_LEGACY", index=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    test_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("execution_id", name="uq_broker_execution_id"),)


class BrokerReconciliationORM(Base):
    __tablename__ = "broker_reconciliations"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(120), ForeignKey("broker_orders.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    broker_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    differences: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    scope_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BrokerRecoveryRunORM(Base):
    __tablename__ = "broker_recovery_runs"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_candidates_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_orders_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broker_orders_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positions_loaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mismatches_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocking_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BrokerIncidentORM(Base):
    __tablename__ = "broker_incidents"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    incident_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN", index=True)
    order_id: Mapped[str | None] = mapped_column(String(120), ForeignKey("broker_orders.id"), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

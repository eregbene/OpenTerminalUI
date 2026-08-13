from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioSnapshotORM(Base):
    __tablename__ = "portfolio_execution_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Canonical logical account key (demo_10k / ftmo_demo_25k / ftmo_demo_50k / ftmo_demo_100k)
    # -- the column every read (latest_snapshot/exposure/can_open_new_trade) filters on.
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Raw MT5 broker login number -- metadata only, never used as a lookup key. Previously this
    # value was written INTO account_id itself (Bug 1: portfolio protection fail-open), so
    # snapshot lookups keyed by the canonical profile id never matched any row.
    mt5_login: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5", index=True)
    account_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO", index=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    free_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    margin: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    margin_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    floating_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    open_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    projected_stop_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    projected_take_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    worst_case_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    expected_gain: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    margin_utilization: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    var_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    maximum_simultaneous_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    exposure_by_currency: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_by_symbol: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_by_strategy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_by_direction: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_by_timeframe: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_by_session: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_matrix: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    position_priority: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    protection_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class ExecutionOrderORM(Base):
    __tablename__ = "portfolio_execution_orders"

    execution_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5", index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_paid: Mapped[float | None] = mapped_column(Float, nullable=True)
    retcode: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    economic_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "idempotency_key", name="uq_portfolio_execution_orders_account_idempotency"),)


class ExecutionStateTransitionORM(Base):
    __tablename__ = "portfolio_execution_state_transitions"

    transition_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    execution_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class ExecutionMetricORM(Base):
    __tablename__ = "portfolio_execution_metrics"

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    window: Mapped[str] = mapped_column(String(32), nullable=False, default="rolling_100", index=True)
    average_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fill_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_slippage: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_spread_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    requotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial_fills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broker_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MT5SchedulerCycleORM(Base):
    __tablename__ = "mt5_scheduler_cycles"

    cycle_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    candle_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    provider_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5_ONLY", index=True)
    symbols_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ai_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    openai_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_send_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5SchedulerCandidateORM(Base):
    __tablename__ = "mt5_scheduler_candidates"

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_class: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    ranking_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    strategy_outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consensus: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    context_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_mt5_candidates_cycle_score", "cycle_id", "ranking_score"),)


class MT5AIDecisionORM(Base):
    __tablename__ = "mt5_ai_decisions"

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0, index=True)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5OrderRecordORM(Base):
    __tablename__ = "mt5_order_records"

    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    intent_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    retcode: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_order_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requested_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5TradeRecordORM(Base):
    __tablename__ = "mt5_trade_records"

    trade_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ai_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    lot_size: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    order_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_tickets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)
    swap: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    close_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    strategy_outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consensus: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    broker_server: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    account_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO", index=True)
    reconciliation_state: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_mt5_trades_symbol_open", "symbol", "open_timestamp"),
        Index("ix_mt5_trades_perf", "symbol", "session", "market_regime", "exit_reason"),
    )


class MT5TradeMemorySnapshotORM(Base):
    __tablename__ = "mt5_trade_memory_snapshots"

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    closed_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    expectancy: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_win: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    mistake_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lessons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False, default="INSUFFICIENT_DATA", index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("scope", "symbol", "session", "market_regime", name="uq_mt5_memory_scope_symbol_session_regime"),
        Index("ix_mt5_memory_lookup", "symbol", "session", "market_regime", "recommendation"),
    )


class MT5CanonicalCandleORM(Base):
    __tablename__ = "mt5_canonical_candles"

    candle_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5", index=True)
    dataset_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5_ONLY", index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    tick_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    real_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality: Mapped[str] = mapped_column(String(32), nullable=False, default="VALID", index=True)
    delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    broker_server: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "broker_symbol", "timeframe", "timestamp", name="uq_mt5_candle_provider_symbol_tf_ts"),
        Index("ix_mt5_candles_symbol_tf_time", "canonical_symbol", "timeframe", "timestamp"),
    )


class MT5RetentionPolicyORM(Base):
    __tablename__ = "mt5_retention_policies"

    policy_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

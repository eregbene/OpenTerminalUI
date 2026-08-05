from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base, SessionLocal, engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AIShadowDecisionORM(Base):
    __tablename__ = "ai_shadow_decisions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    shadow_trade_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(96), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    context_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(80), nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposed_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_risk_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_approved_position_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_theoretical_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    risk_status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    hourly_request_count: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    daily_request_count: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    shadow_status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    theoretical_entry_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    theoretical_exit_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    theoretical_exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    theoretical_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_decision: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AITradingCycleORM(Base):
    __tablename__ = "ai_trading_cycles"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    deterministic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_calls: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    place_order_calls: Mapped[int] = mapped_column(Float, nullable=False, default=0)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AITradingStateORM(Base):
    __tablename__ = "ai_trading_state"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


def ensure_tables() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("ai_shadow_decisions"):
        Base.metadata.create_all(bind=engine, tables=[AIShadowDecisionORM.__table__, AITradingCycleORM.__table__, AITradingStateORM.__table__])
        return
    if not inspector.has_table("ai_trading_cycles"):
        Base.metadata.create_all(bind=engine, tables=[AITradingCycleORM.__table__])
    if not inspector.has_table("ai_trading_state"):
        Base.metadata.create_all(bind=engine, tables=[AITradingStateORM.__table__])
    existing = {column["name"] for column in inspector.get_columns("ai_shadow_decisions")}
    required = {
        "estimated_cost_usd": "FLOAT NOT NULL DEFAULT 0",
        "hourly_request_count": "FLOAT NOT NULL DEFAULT 0",
        "daily_request_count": "FLOAT NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE ai_shadow_decisions ADD COLUMN {name} {ddl}"))


def save_decision(payload: dict[str, Any]) -> None:
    ensure_tables()
    with SessionLocal() as session:
        existing = session.get(AIShadowDecisionORM, payload["id"])
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            session.add(AIShadowDecisionORM(**payload))
        session.commit()


def list_decisions(limit: int = 50) -> list[AIShadowDecisionORM]:
    ensure_tables()
    with SessionLocal() as session:
        return list(session.scalars(select(AIShadowDecisionORM).order_by(AIShadowDecisionORM.created_at.desc()).limit(limit)).all())


def get_decision(decision_id: str) -> AIShadowDecisionORM | None:
    ensure_tables()
    with SessionLocal() as session:
        return session.get(AIShadowDecisionORM, decision_id)


def save_cycle(payload: dict[str, Any]) -> None:
    ensure_tables()
    with SessionLocal() as session:
        existing = session.get(AITradingCycleORM, payload["id"])
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            session.add(AITradingCycleORM(**payload))
        session.commit()


def get_cycle(cycle_id: str) -> AITradingCycleORM | None:
    ensure_tables()
    with SessionLocal() as session:
        return session.get(AITradingCycleORM, cycle_id)


def get_state(key: str) -> dict[str, Any]:
    ensure_tables()
    with SessionLocal() as session:
        row = session.get(AITradingStateORM, key)
        return dict(row.value) if row else {}


def set_state(key: str, value: dict[str, Any]) -> None:
    ensure_tables()
    with SessionLocal() as session:
        row = session.get(AITradingStateORM, key)
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            session.add(AITradingStateORM(key=key, value=value))
        session.commit()

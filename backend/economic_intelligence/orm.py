from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EconomicEventORM(Base):
    __tablename__ = "ff_economic_events"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    impact: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_scheduled_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forecast_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    forecast_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled", index=True)
    is_central_bank_event: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_ff_event_provider_id"),
        Index("ix_ff_economic_events_currency_time", "currency", "scheduled_at_utc"),
    )


class EconomicEventSnapshotORM(Base):
    __tablename__ = "ff_economic_event_snapshots"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    economic_event_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impact: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    actual_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forecast_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_ff_economic_event_snapshots_event_observed", "economic_event_id", "observed_at"),)


class EconomicEventRevisionORM(Base):
    __tablename__ = "ff_economic_event_revisions"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    economic_event_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class EconomicEventDefinitionORM(Base):
    __tablename__ = "ff_economic_event_definitions"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_traders_care: Mapped[str | None] = mapped_column(Text, nullable=True)
    usual_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_release: Mapped[str | None] = mapped_column(String(64), nullable=True)
    derived_via: Mapped[str | None] = mapped_column(Text, nullable=True)
    higher_is_positive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    affected_assets_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    default_blackout_before_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_blackout_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (UniqueConstraint("normalized_name", name="uq_ff_event_definition_name"),)


class EconomicNewsItemORM(Base):
    __tablename__ = "ff_economic_news_items"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider_story_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_headline: Mapped[str] = mapped_column(Text, nullable=False)
    published_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    source_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    forex_factory_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_currencies_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    provider_impact: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("content_hash", name="uq_ff_news_content_hash"),)


class EconomicNewsClassificationORM(Base):
    __tablename__ = "ff_economic_news_classifications"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    news_item_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    model_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_currencies_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_symbols_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    directional_bias_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    urgency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_constraints_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_model_response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class EconomicTradeContextSnapshotORM(Base):
    __tablename__ = "ff_economic_trade_context_snapshots"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trading_cycle_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    strategy_signal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    calendar_context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    news_context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    openai_context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    deterministic_decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class EconomicProviderRunORM(Base):
    __tablename__ = "ff_economic_provider_runs"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", index=True)
    records_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EconomicProviderStateORM(Base):
    __tablename__ = "ff_economic_provider_state"

    provider: Mapped[str] = mapped_column(String(48), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", index=True)
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_scheduled_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_known_good_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

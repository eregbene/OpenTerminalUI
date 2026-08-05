from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EconomicEventORM(Base):
    __tablename__ = "economic_events"

    internal_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    provider_value_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    calendar_change_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    event_category: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    scheduled_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    forecast_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    revised_previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", index=True)
    affected_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    raw_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False, default="currency_rules_v1")

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", "provider_value_id", "scheduled_at_utc", name="uq_economic_event_provider_value_time"),
        Index("ix_economic_events_currency_time", "currency", "scheduled_at_utc"),
    )


class EconomicEventRevisionORM(Base):
    __tablename__ = "economic_event_revisions"

    revision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    event_internal_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    forecast_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    revised_previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", index=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class NewsItemORM(Base):
    __tablename__ = "news_items"

    internal_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_article_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    publisher: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    permitted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    countries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    currencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_currency_sentiment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    normalized_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0, index=True)
    impact: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    freshness: Mapped[str] = mapped_column(String(16), nullable=False, default="fresh", index=True)
    duplicate_group_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    content_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False, default="currency_rules_v1")
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False, default="news_score_v1")

    __table_args__ = (UniqueConstraint("provider", "content_fingerprint", name="uq_news_provider_fingerprint"),)


class NewsClusterORM(Base):
    __tablename__ = "news_clusters"

    cluster_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    canonical_headline: Mapped[str] = mapped_column(Text, nullable=False)
    earliest_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    earliest_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    latest_update_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    providers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    publishers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    impact: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    normalized_sentiment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conflicting_sentiment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    relevance_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False, default="news_score_v1")


class MacroSeriesDefinitionORM(Base):
    __tablename__ = "macro_series_definitions"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="fred", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MacroObservationORM(Base):
    __tablename__ = "macro_observations"

    observation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    observation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_known_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    realtime_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    realtime_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vintage_date: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    revision_state: Mapped[str] = mapped_column(String(24), nullable=False, default="initial", index=True)
    trend: Mapped[str] = mapped_column(String(24), nullable=False, default="insufficient_data", index=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (UniqueConstraint("series_id", "observation_date", "vintage_date", name="uq_macro_obs_series_date_vintage"),)


class ContextProviderStateORM(Base):
    __tablename__ = "context_provider_states"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", index=True)
    records_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rate_limit_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_scheduled_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class DecisionContextSnapshotORM(Base):
    __tablename__ = "decision_context_snapshots"

    context_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    technical_data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    mt5_calendar_freshness: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", index=True)
    provider_coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    upcoming_relevant_events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active_event_window: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    headline_clusters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    base_sentiment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    quote_sentiment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    relative_sentiment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conflicting_sentiment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    base_macro_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    quote_macro_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scheduled_event_risk: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    headline_risk: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    combined_context_risk_state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    block_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    acknowledgement_requirements: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False, default="currency_rules_v1")
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False, default="context_score_v1")
    risk_rule_version: Mapped[str] = mapped_column(String(32), nullable=False, default="context_risk_v1")

    __table_args__ = (Index("ix_context_snapshots_symbol_created", "symbol", "created_at"),)

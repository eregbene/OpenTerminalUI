"""decision context

Revision ID: 0021_decision_context
Revises: 0020_mt5_autonomous_persistence
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_decision_context"
down_revision = "0020_mt5_autonomous_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economic_events",
        sa.Column("internal_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(96), nullable=False),
        sa.Column("provider_value_id", sa.String(96), nullable=True),
        sa.Column("calendar_change_id", sa.String(96), nullable=True),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("event_code", sa.String(64), nullable=True),
        sa.Column("country", sa.String(96), nullable=True),
        sa.Column("country_code", sa.String(8), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("event_category", sa.String(96), nullable=True),
        sa.Column("importance", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("scheduled_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_timezone", sa.String(64), nullable=True),
        sa.Column("scheduled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(64), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("forecast_value", sa.Float(), nullable=True),
        sa.Column("previous_value", sa.Float(), nullable=True),
        sa.Column("revised_previous_value", sa.Float(), nullable=True),
        sa.Column("value_unit", sa.String(32), nullable=True),
        sa.Column("multiplier", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("affected_symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_hash", sa.String(128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("mapping_version", sa.String(32), nullable=False, server_default="currency_rules_v1"),
        sa.UniqueConstraint("provider", "provider_event_id", "provider_value_id", "scheduled_at_utc", name="uq_economic_event_provider_value_time"),
    )
    for col in ("provider", "provider_event_id", "provider_value_id", "calendar_change_id", "country", "country_code", "currency", "event_category", "importance", "scheduled_at_utc", "status", "received_at", "raw_payload_hash"):
        op.create_index(f"ix_economic_events_{col}", "economic_events", [col])
    op.create_index("ix_economic_events_currency_time", "economic_events", ["currency", "scheduled_at_utc"])

    op.create_table(
        "economic_event_revisions",
        sa.Column("revision_id", sa.String(160), primary_key=True),
        sa.Column("event_internal_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("forecast_value", sa.Float(), nullable=True),
        sa.Column("previous_value", sa.Float(), nullable=True),
        sa.Column("revised_previous_value", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("raw_payload_hash", sa.String(128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    for col in ("event_internal_id", "provider", "received_at", "status", "raw_payload_hash"):
        op.create_index(f"ix_economic_event_revisions_{col}", "economic_event_revisions", [col])

    op.create_table(
        "news_items",
        sa.Column("internal_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_article_id", sa.String(160), nullable=True),
        sa.Column("publisher", sa.String(160), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("permitted_summary", sa.Text(), nullable=True),
        sa.Column("article_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("countries", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("currencies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("affected_symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("topics", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("provider_sentiment", sa.String(32), nullable=True),
        sa.Column("provider_sentiment_score", sa.Float(), nullable=True),
        sa.Column("normalized_currency_sentiment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("normalized_sentiment_score", sa.Float(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impact", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("freshness", sa.String(16), nullable=False, server_default="fresh"),
        sa.Column("duplicate_group_id", sa.String(160), nullable=True),
        sa.Column("content_fingerprint", sa.String(128), nullable=False),
        sa.Column("raw_payload_hash", sa.String(128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mapping_version", sa.String(32), nullable=False, server_default="currency_rules_v1"),
        sa.Column("scoring_version", sa.String(32), nullable=False, server_default="news_score_v1"),
        sa.UniqueConstraint("provider", "content_fingerprint", name="uq_news_provider_fingerprint"),
    )
    for col in ("provider", "provider_article_id", "publisher", "published_at", "received_at", "language", "event_type", "provider_sentiment", "relevance_score", "impact", "freshness", "duplicate_group_id", "content_fingerprint", "raw_payload_hash"):
        op.create_index(f"ix_news_items_{col}", "news_items", [col])

    op.create_table(
        "news_clusters",
        sa.Column("cluster_id", sa.String(160), primary_key=True),
        sa.Column("canonical_headline", sa.Text(), nullable=False),
        sa.Column("earliest_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("earliest_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_update_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("publishers", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("currencies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("affected_symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("impact", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("normalized_sentiment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("conflicting_sentiment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("relevance_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("scoring_version", sa.String(32), nullable=False, server_default="news_score_v1"),
    )
    for col in ("earliest_published_at", "earliest_received_at", "latest_update_at", "impact", "conflicting_sentiment"):
        op.create_index(f"ix_news_clusters_{col}", "news_clusters", [col])

    op.create_table("macro_series_definitions", sa.Column("series_id", sa.String(64), primary_key=True), sa.Column("currency", sa.String(8), nullable=False), sa.Column("name", sa.Text(), nullable=False), sa.Column("provider", sa.String(16), nullable=False, server_default="fred"), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("currency", "provider", "enabled"):
        op.create_index(f"ix_macro_series_definitions_{col}", "macro_series_definitions", [col])

    op.create_table(
        "macro_observations",
        sa.Column("observation_id", sa.String(160), primary_key=True),
        sa.Column("series_id", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("observation_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("previous_known_value", sa.Float(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("realtime_start", sa.String(32), nullable=True),
        sa.Column("realtime_end", sa.String(32), nullable=True),
        sa.Column("vintage_date", sa.String(32), nullable=True),
        sa.Column("revision_state", sa.String(24), nullable=False, server_default="initial"),
        sa.Column("trend", sa.String(24), nullable=False, server_default="insufficient_data"),
        sa.Column("raw_payload_hash", sa.String(128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("series_id", "observation_date", "vintage_date", name="uq_macro_obs_series_date_vintage"),
    )
    for col in ("series_id", "currency", "observation_date", "received_at", "vintage_date", "revision_state", "trend", "raw_payload_hash"):
        op.create_index(f"ix_macro_observations_{col}", "macro_observations", [col])

    op.create_table("context_provider_states", sa.Column("provider", sa.String(32), primary_key=True), sa.Column("job_type", sa.String(64), primary_key=True), sa.Column("status", sa.String(32), nullable=False, server_default="unknown"), sa.Column("records_requested", sa.Integer(), nullable=False, server_default="0"), sa.Column("records_received", sa.Integer(), nullable=False, server_default="0"), sa.Column("records_created", sa.Integer(), nullable=False, server_default="0"), sa.Column("records_updated", sa.Integer(), nullable=False, server_default="0"), sa.Column("duplicates", sa.Integer(), nullable=False, server_default="0"), sa.Column("errors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("rate_limit_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("last_successful_sync", sa.DateTime(timezone=True), nullable=True), sa.Column("next_scheduled_sync", sa.DateTime(timezone=True), nullable=True), sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("status", "last_successful_sync", "next_scheduled_sync"):
        op.create_index(f"ix_context_provider_states_{col}", "context_provider_states", [col])

    op.create_table(
        "decision_context_snapshots",
        sa.Column("context_id", sa.String(160), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("quote_currency", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("technical_data_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mt5_calendar_freshness", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("provider_coverage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("upcoming_relevant_events", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("active_event_window", sa.JSON(), nullable=True),
        sa.Column("headline_clusters", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("base_sentiment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("quote_sentiment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("relative_sentiment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("conflicting_sentiment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_macro_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("quote_macro_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("scheduled_event_risk", sa.String(48), nullable=False),
        sa.Column("headline_risk", sa.String(48), nullable=False),
        sa.Column("combined_context_risk_state", sa.String(64), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("block_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("acknowledgement_requirements", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("mapping_version", sa.String(32), nullable=False, server_default="currency_rules_v1"),
        sa.Column("scoring_version", sa.String(32), nullable=False, server_default="context_score_v1"),
        sa.Column("risk_rule_version", sa.String(32), nullable=False, server_default="context_risk_v1"),
    )
    for col in ("symbol", "base_currency", "quote_currency", "created_at", "valid_until", "technical_data_timestamp", "mt5_calendar_freshness", "conflicting_sentiment", "scheduled_event_risk", "headline_risk", "combined_context_risk_state"):
        op.create_index(f"ix_decision_context_snapshots_{col}", "decision_context_snapshots", [col])
    op.create_index("ix_context_snapshots_symbol_created", "decision_context_snapshots", ["symbol", "created_at"])


def downgrade() -> None:
    for table in ("decision_context_snapshots", "context_provider_states", "macro_observations", "macro_series_definitions", "news_clusters", "news_items", "economic_event_revisions", "economic_events"):
        op.drop_table(table)

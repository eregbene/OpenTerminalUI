"""forex factory economic intelligence layer

Revision ID: 0028_economic_intelligence
Revises: 0027_lineage_and_reconciliation
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_economic_intelligence"
down_revision = "0027_lineage_and_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ff_economic_events",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("impact", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("scheduled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timezone", sa.String(64), nullable=True),
        sa.Column("raw_scheduled_at", sa.String(64), nullable=True),
        sa.Column("actual_raw", sa.String(64), nullable=True),
        sa.Column("forecast_raw", sa.String(64), nullable=True),
        sa.Column("previous_raw", sa.String(64), nullable=True),
        sa.Column("actual_numeric", sa.Float(), nullable=True),
        sa.Column("forecast_numeric", sa.Float(), nullable=True),
        sa.Column("previous_numeric", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("detail_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("is_central_bank_event", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload_hash", sa.String(128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_ff_event_provider_id"),
    )
    for col in ("provider", "provider_event_id", "normalized_name", "currency", "impact", "scheduled_at_utc", "status", "is_central_bank_event", "payload_hash", "first_seen_at", "last_seen_at"):
        op.create_index(f"ix_ff_economic_events_{col}", "ff_economic_events", [col])
    op.create_index("ix_ff_economic_events_currency_time", "ff_economic_events", ["currency", "scheduled_at_utc"])

    op.create_table(
        "ff_economic_event_snapshots",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("economic_event_id", sa.String(160), nullable=False),
        sa.Column("scheduled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impact", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("actual_raw", sa.String(64), nullable=True),
        sa.Column("forecast_raw", sa.String(64), nullable=True),
        sa.Column("previous_raw", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    for col in ("economic_event_id", "observed_at", "payload_hash"):
        op.create_index(f"ix_ff_economic_event_snapshots_{col}", "ff_economic_event_snapshots", [col])
    op.create_index("ix_ff_economic_event_snapshots_event_observed", "ff_economic_event_snapshots", ["economic_event_id", "observed_at"])

    op.create_table(
        "ff_economic_event_revisions",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("economic_event_id", sa.String(160), nullable=False),
        sa.Column("field_name", sa.String(32), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("economic_event_id", "field_name", "observed_at"):
        op.create_index(f"ix_ff_economic_event_revisions_{col}", "ff_economic_event_revisions", [col])

    op.create_table(
        "ff_economic_event_definitions",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("source_name", sa.String(160), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("why_traders_care", sa.Text(), nullable=True),
        sa.Column("usual_effect", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(64), nullable=True),
        sa.Column("next_release", sa.String(64), nullable=True),
        sa.Column("derived_via", sa.Text(), nullable=True),
        sa.Column("higher_is_positive", sa.Boolean(), nullable=True),
        sa.Column("affected_assets_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("default_blackout_before_minutes", sa.Integer(), nullable=True),
        sa.Column("default_blackout_after_minutes", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_hash", sa.String(128), nullable=True),
        sa.UniqueConstraint("normalized_name", name="uq_ff_event_definition_name"),
    )
    op.create_index("ix_ff_economic_event_definitions_normalized_name", "ff_economic_event_definitions", ["normalized_name"])

    op.create_table(
        "ff_economic_news_items",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("provider_story_id", sa.String(160), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("normalized_headline", sa.Text(), nullable=False),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_name", sa.String(160), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("forex_factory_url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("related_currencies_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("provider_impact", sa.String(16), nullable=True),
        sa.Column("preview", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_hash", name="uq_ff_news_content_hash"),
    )
    for col in ("provider_story_id", "published_at_utc", "provider_impact", "content_hash", "first_seen_at", "last_seen_at"):
        op.create_index(f"ix_ff_economic_news_items_{col}", "ff_economic_news_items", [col])

    op.create_table(
        "ff_economic_news_classifications",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("news_item_id", sa.String(160), nullable=False),
        sa.Column("model_provider", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("model_name", sa.String(64), nullable=True),
        sa.Column("affected_currencies_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("affected_symbols_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("directional_bias_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("urgency", sa.String(16), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("recommended_constraints_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("raw_model_response_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("news_item_id", "risk_level", "created_at"):
        op.create_index(f"ix_ff_economic_news_classifications_{col}", "ff_economic_news_classifications", [col])

    op.create_table(
        "ff_economic_trade_context_snapshots",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("trading_cycle_id", sa.String(160), nullable=True),
        sa.Column("strategy_signal_id", sa.String(160), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("calendar_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("news_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("openai_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("deterministic_decision", sa.String(32), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("trading_cycle_id", "strategy_signal_id", "symbol", "deterministic_decision", "created_at"):
        op.create_index(f"ix_ff_economic_trade_context_snapshots_{col}", "ff_economic_trade_context_snapshots", [col])

    op.create_table(
        "ff_economic_provider_runs",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("provider_name", sa.String(48), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("records_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("freshness_seconds", sa.Float(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    for col in ("provider_name", "started_at", "status"):
        op.create_index(f"ix_ff_economic_provider_runs_{col}", "ff_economic_provider_runs", [col])

    op.create_table(
        "ff_economic_provider_state",
        sa.Column("provider", sa.String(48), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("last_successful_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scheduled_sync", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("last_known_good_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("status", "last_successful_sync"):
        op.create_index(f"ix_ff_economic_provider_state_{col}", "ff_economic_provider_state", [col])

    op.add_column("portfolio_execution_orders", sa.Column("economic_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("portfolio_execution_orders", "economic_context")
    for table in ("ff_economic_provider_state", "ff_economic_provider_runs", "ff_economic_trade_context_snapshots", "ff_economic_news_classifications", "ff_economic_news_items", "ff_economic_event_definitions", "ff_economic_event_revisions", "ff_economic_event_snapshots", "ff_economic_events"):
        op.drop_table(table)

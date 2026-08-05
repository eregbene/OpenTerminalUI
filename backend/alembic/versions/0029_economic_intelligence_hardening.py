"""forex factory economic intelligence hardening phase

Revision ID: 0029_economic_intelligence_hardening
Revises: 0028_economic_intelligence
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_economic_intelligence_hardening"
down_revision = "0028_economic_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ff_economic_events -- lifecycle tracking
    op.add_column("ff_economic_events", sa.Column("lifecycle_status", sa.String(24), nullable=False, server_default="scheduled"))
    op.add_column("ff_economic_events", sa.Column("forecast_first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ff_economic_events", sa.Column("actual_first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ff_economic_events", sa.Column("previous_revision_first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ff_economic_events", sa.Column("scheduled_time_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ff_economic_events", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_ff_economic_events_lifecycle_status", "ff_economic_events", ["lifecycle_status"])

    # ff_economic_trade_context_snapshots -- shadow mode
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("economic_guard_mode", sa.String(16), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("shadow_decision", sa.String(32), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("effective_decision", sa.String(32), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("execution_changed_by_economic", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("nearest_event_id", sa.String(160), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("minutes_to_event", sa.Float(), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("provider_freshness_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("spread_at_evaluation", sa.Float(), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("order_outcome", sa.JSON(), nullable=True))
    op.add_column("ff_economic_trade_context_snapshots", sa.Column("linked_execution_id", sa.String(160), nullable=True))
    for col in ("economic_guard_mode", "shadow_decision", "effective_decision", "linked_execution_id"):
        op.create_index(f"ix_ff_economic_trade_context_snapshots_{col}", "ff_economic_trade_context_snapshots", [col])
    # Postgres identifiers are capped at 63 chars -- the auto-generated
    # "ix_ff_economic_trade_context_snapshots_execution_changed_by_economic" (68 chars) exceeds
    # that, so this one column gets a manually shortened name.
    op.create_index("ix_ff_econ_ctx_snap_execution_changed", "ff_economic_trade_context_snapshots", ["execution_changed_by_economic"])

    # ff_economic_provider_state -- diagnostics
    op.add_column("ff_economic_provider_state", sa.Column("selector_version", sa.String(32), nullable=True))
    op.add_column("ff_economic_provider_state", sa.Column("records_accepted", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ff_economic_provider_state", sa.Column("records_rejected", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ff_economic_provider_state", sa.Column("cache_hits", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ff_economic_provider_state", sa.Column("cache_misses", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ff_economic_provider_state", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ff_economic_provider_state", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ff_economic_provider_state", sa.Column("lock_owner", sa.String(64), nullable=True))

    # new cache-hit/miss audit log
    op.create_table(
        "ff_economic_event_definition_cache_log",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ff_economic_event_definition_cache_log_normalized_name", "ff_economic_event_definition_cache_log", ["normalized_name"])
    op.create_index("ix_ff_economic_event_definition_cache_log_outcome", "ff_economic_event_definition_cache_log", ["outcome"])
    op.create_index("ix_ff_economic_event_definition_cache_log_observed_at", "ff_economic_event_definition_cache_log", ["observed_at"])


def downgrade() -> None:
    op.drop_table("ff_economic_event_definition_cache_log")

    for col in ("selector_version", "records_accepted", "records_rejected", "cache_hits", "cache_misses", "retry_count", "last_attempt_at", "lock_owner"):
        op.drop_column("ff_economic_provider_state", col)

    op.drop_index("ix_ff_econ_ctx_snap_execution_changed", table_name="ff_economic_trade_context_snapshots")
    for col in ("economic_guard_mode", "shadow_decision", "effective_decision", "linked_execution_id"):
        op.drop_index(f"ix_ff_economic_trade_context_snapshots_{col}", table_name="ff_economic_trade_context_snapshots")
    for col in ("economic_guard_mode", "shadow_decision", "effective_decision", "execution_changed_by_economic", "nearest_event_id", "minutes_to_event", "provider_freshness_json", "spread_at_evaluation", "order_outcome", "linked_execution_id"):
        op.drop_column("ff_economic_trade_context_snapshots", col)

    op.drop_index("ix_ff_economic_events_lifecycle_status", table_name="ff_economic_events")
    for col in ("lifecycle_status", "forecast_first_seen_at", "actual_first_seen_at", "previous_revision_first_seen_at", "scheduled_time_changed_at", "completed_at"):
        op.drop_column("ff_economic_events", col)

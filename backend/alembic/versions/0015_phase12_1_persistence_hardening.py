"""phase 12.1 persistence hardening

Revision ID: 0015_phase12_1_persistence_hardening
Revises: 0014_phase12_portfolio_ops
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_phase12_1_persistence_hardening"
down_revision = "0014_phase12_portfolio_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "phase12_attribution_records",
    sa.Column("attribution_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("dimension", sa.String(32), nullable=False, index=True),
    sa.Column("bucket", sa.String(128), nullable=False, index=True),
    sa.Column("pnl", sa.Float(), nullable=False),
    sa.Column("residual", sa.Float(), nullable=False),
    sa.Column("currency", sa.String(8), nullable=False),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_index("ix_phase12_attribution_portfolio_dimension_asof", "phase12_attribution_records", ["portfolio_id", "dimension", "as_of"])
  op.create_table(
    "phase12_risk_snapshots",
    sa.Column("risk_snapshot_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("gross_exposure", sa.Float(), nullable=False),
    sa.Column("net_exposure", sa.Float(), nullable=False),
    sa.Column("leverage", sa.Float(), nullable=False),
    sa.Column("margin_utilization", sa.Float(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_alert_rules",
    sa.Column("rule_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
    sa.Column("scope", sa.String(64), nullable=False, index=True),
    sa.Column("severity", sa.String(16), nullable=False, index=True),
    sa.Column("enabled", sa.Boolean(), nullable=False, index=True),
    sa.Column("conditions_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_table(
    "phase12_incident_timeline_entries",
    sa.Column("timeline_entry_id", sa.String(64), primary_key=True),
    sa.Column("incident_id", sa.String(64), sa.ForeignKey("phase12_incidents.incident_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("actor_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("event_type", sa.String(48), nullable=False, index=True),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_report_schedules",
    sa.Column("schedule_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=True, index=True),
    sa.Column("report_type", sa.String(48), nullable=False, index=True),
    sa.Column("frequency", sa.String(16), nullable=False, index=True),
    sa.Column("timezone", sa.String(64), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False, index=True),
    sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True, index=True),
    sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True, index=True),
    sa.Column("retry_policy_json", sa.JSON(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_table(
    "phase12_replay_sessions",
    sa.Column("session_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("cursor_sequence", sa.Integer(), nullable=False),
    sa.Column("speed", sa.Float(), nullable=False),
    sa.Column("filters_json", sa.JSON(), nullable=False),
    sa.Column("read_only", sa.Boolean(), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_table(
    "phase12_journal_entries",
    sa.Column("journal_entry_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=False, index=True),
    sa.Column("strategy_deployment_id", sa.String(64), nullable=True, index=True),
    sa.Column("instrument_id", sa.String(64), nullable=False, index=True),
    sa.Column("source_trade_id", sa.String(128), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("tags_json", sa.JSON(), nullable=False),
    sa.Column("facts_json", sa.JSON(), nullable=False),
    sa.Column("metrics_json", sa.JSON(), nullable=False),
    sa.Column("ai_narrative_json", sa.JSON(), nullable=False),
    sa.Column("evidence_json", sa.JSON(), nullable=False),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("portfolio_id", "source_trade_id", name="uq_phase12_journal_trade"),
  )
  op.create_table(
    "phase12_journal_notes",
    sa.Column("note_id", sa.String(64), primary_key=True),
    sa.Column("journal_entry_id", sa.String(64), sa.ForeignKey("phase12_journal_entries.journal_entry_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("version", sa.Integer(), nullable=False, index=True),
    sa.Column("note_text", sa.Text(), nullable=False),
    sa.Column("tags_json", sa.JSON(), nullable=False),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )


def downgrade() -> None:
  for table in [
    "phase12_journal_notes",
    "phase12_journal_entries",
    "phase12_replay_sessions",
    "phase12_report_schedules",
    "phase12_incident_timeline_entries",
    "phase12_alert_rules",
    "phase12_risk_snapshots",
    "phase12_attribution_records",
  ]:
    op.drop_table(table)

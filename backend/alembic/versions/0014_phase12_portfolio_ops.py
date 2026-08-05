"""phase 12 portfolio operations

Revision ID: 0014_phase12_portfolio_ops
Revises: 0013_canonical_paper_trading
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_phase12_portfolio_ops"
down_revision = "0013_canonical_paper_trading"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "phase12_portfolios",
    sa.Column("portfolio_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
    sa.Column("name", sa.String(160), nullable=False, index=True),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("base_currency", sa.String(8), nullable=False),
    sa.Column("account_ids", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("risk_policy_id", sa.String(64), nullable=True),
    sa.Column("allocation_policy_id", sa.String(64), nullable=True),
    sa.Column("paper_only", sa.Boolean(), nullable=False, index=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_table(
    "phase12_portfolio_memberships",
    sa.Column("membership_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("deployment_id", sa.String(64), nullable=False, index=True),
    sa.Column("strategy_implementation_id", sa.String(64), nullable=False, index=True),
    sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("instrument_scope", sa.JSON(), nullable=False),
    sa.Column("timeframe", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("activation_date", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deactivation_date", sa.DateTime(timezone=True), nullable=True),
    sa.Column("allocation", sa.JSON(), nullable=False),
    sa.Column("risk_contribution_limit", sa.Float(), nullable=True),
    sa.Column("owner", sa.String(64), nullable=False),
    sa.Column("evidence_lineage", sa.JSON(), nullable=False),
    sa.Column("approved", sa.Boolean(), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.UniqueConstraint("portfolio_id", "deployment_id", "version", name="uq_phase12_membership_version"),
  )
  op.create_table(
    "phase12_strategy_allocations",
    sa.Column("allocation_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("deployment_id", sa.String(64), nullable=False, index=True),
    sa.Column("allocation_type", sa.String(32), nullable=False, index=True),
    sa.Column("allocation_amount", sa.Float(), nullable=False),
    sa.Column("effective_date", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("expiry_date", sa.DateTime(timezone=True), nullable=True, index=True),
    sa.Column("max_gross_exposure", sa.Float(), nullable=False),
    sa.Column("max_net_exposure", sa.Float(), nullable=False),
    sa.Column("max_position_size", sa.Float(), nullable=False),
    sa.Column("max_daily_loss", sa.Float(), nullable=False),
    sa.Column("max_drawdown", sa.Float(), nullable=False),
    sa.Column("approval_record", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("version", sa.Integer(), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_snapshots",
    sa.Column("snapshot_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("account_id", sa.String(64), nullable=True, index=True),
    sa.Column("category", sa.String(48), nullable=False, index=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("source_version", sa.String(64), nullable=False),
    sa.Column("valuation_status", sa.String(32), nullable=False, index=True),
    sa.Column("currency", sa.String(8), nullable=False),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("supersedes_snapshot_id", sa.String(64), nullable=True, index=True),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.UniqueConstraint("portfolio_id", "category", "content_hash", name="uq_phase12_snapshot_hash"),
  )
  op.create_table(
    "phase12_ledger_entries",
    sa.Column("ledger_entry_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("account_id", sa.String(64), nullable=True, index=True),
    sa.Column("strategy_deployment_id", sa.String(64), nullable=True, index=True),
    sa.Column("instrument_id", sa.String(64), nullable=True, index=True),
    sa.Column("entry_type", sa.String(40), nullable=False, index=True),
    sa.Column("source_event", sa.String(64), nullable=False, index=True),
    sa.Column("source_identifier", sa.String(128), nullable=False, index=True),
    sa.Column("currency", sa.String(8), nullable=False),
    sa.Column("amount", sa.Float(), nullable=False),
    sa.Column("quantity_delta", sa.Float(), nullable=False),
    sa.Column("price", sa.Float(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("audit_reference", sa.String(128), nullable=True),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.UniqueConstraint("source_event", "source_identifier", "entry_type", name="uq_phase12_ledger_source"),
  )
  op.create_table(
    "phase12_performance_snapshots",
    sa.Column("snapshot_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), sa.ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("window", sa.String(32), nullable=False, index=True),
    sa.Column("frequency", sa.String(32), nullable=False),
    sa.Column("currency", sa.String(8), nullable=False),
    sa.Column("metrics_json", sa.JSON(), nullable=False),
    sa.Column("data_completeness", sa.String(32), nullable=False, index=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_execution_quality",
    sa.Column("execution_quality_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), nullable=False, index=True),
    sa.Column("order_id", sa.String(64), nullable=False, index=True),
    sa.Column("side", sa.String(8), nullable=False, index=True),
    sa.Column("quantity", sa.Float(), nullable=False),
    sa.Column("decision_price", sa.Float(), nullable=True),
    sa.Column("fill_price", sa.Float(), nullable=True),
    sa.Column("benchmark_json", sa.JSON(), nullable=False),
    sa.Column("latency_json", sa.JSON(), nullable=False),
    sa.Column("slippage_bps", sa.Float(), nullable=True),
    sa.Column("commission", sa.Float(), nullable=False),
    sa.Column("anomaly_flags", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_alerts",
    sa.Column("alert_id", sa.String(64), primary_key=True),
    sa.Column("scope", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=True, index=True),
    sa.Column("rule_id", sa.String(64), nullable=False, index=True),
    sa.Column("severity", sa.String(16), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("fingerprint", sa.String(128), nullable=False, index=True),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_incidents",
    sa.Column("incident_id", sa.String(64), primary_key=True),
    sa.Column("portfolio_id", sa.String(64), nullable=True, index=True),
    sa.Column("title", sa.String(240), nullable=False, index=True),
    sa.Column("severity", sa.String(16), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("timeline_json", sa.JSON(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
  )
  op.create_table(
    "phase12_reports",
    sa.Column("report_id", sa.String(64), primary_key=True),
    sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=True, index=True),
    sa.Column("report_type", sa.String(48), nullable=False, index=True),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("content_hash", sa.String(128), nullable=False, index=True),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_replay_events",
    sa.Column("event_id", sa.String(64), primary_key=True),
    sa.Column("session_id", sa.String(64), nullable=False, index=True),
    sa.Column("portfolio_id", sa.String(64), nullable=False, index=True),
    sa.Column("sequence", sa.Integer(), nullable=False, index=True),
    sa.Column("event_type", sa.String(64), nullable=False, index=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )
  op.create_table(
    "phase12_operational_health",
    sa.Column("health_id", sa.String(64), primary_key=True),
    sa.Column("component", sa.String(64), nullable=False, index=True),
    sa.Column("status", sa.String(24), nullable=False, index=True),
    sa.Column("freshness_seconds", sa.Float(), nullable=True),
    sa.Column("details_json", sa.JSON(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, index=True),
  )


def downgrade() -> None:
  for table in [
    "phase12_operational_health",
    "phase12_replay_events",
    "phase12_reports",
    "phase12_incidents",
    "phase12_alerts",
    "phase12_execution_quality",
    "phase12_performance_snapshots",
    "phase12_ledger_entries",
    "phase12_snapshots",
    "phase12_strategy_allocations",
    "phase12_portfolio_memberships",
    "phase12_portfolios",
  ]:
    op.drop_table(table)

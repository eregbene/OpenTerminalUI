"""ibkr paper acceptance persistence

Revision ID: 0018_ibkr_paper_acceptance_persistence
Revises: 0017_forex_framework_signals
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_ibkr_paper_acceptance_persistence"
down_revision = "0017_forex_framework_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_connection_sessions",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("session_id", sa.String(120), nullable=False, unique=True, index=True),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True, index=True),
        sa.Column("masked_account_id", sa.String(64), nullable=True),
        sa.Column("account_id_hash", sa.String(128), nullable=True, index=True),
        sa.Column("connection_state", sa.String(48), nullable=False, index=True),
        sa.Column("account_verified", sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column("market_data_mode", sa.String(32), nullable=False, default="UNKNOWN"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_server_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broker_contracts",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("canonical_symbol", sa.String(32), nullable=False, index=True),
        sa.Column("con_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("security_type", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("local_symbol", sa.String(80), nullable=True),
        sa.Column("trading_class", sa.String(80), nullable=True),
        sa.Column("minimum_tick", sa.Numeric(20, 10), nullable=True),
        sa.Column("quantity_increment", sa.Numeric(20, 6), nullable=True),
        sa.Column("market_rule_ids", sa.JSON(), nullable=False),
        sa.Column("contract_payload", sa.JSON(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_source", sa.String(32), nullable=False, index=True),
        sa.Column("content_hash", sa.String(128), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("broker", "environment", "canonical_symbol", "con_id", name="uq_broker_contract_symbol_conid"),
    )
    op.create_index("ix_broker_contract_lookup", "broker_contracts", ["broker", "environment", "canonical_symbol", "verified"])
    op.create_table(
        "broker_orders",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("candidate_id", sa.String(120), nullable=True, index=True),
        sa.Column("risk_decision_id", sa.String(120), nullable=True, index=True),
        sa.Column("oms_intent_id", sa.String(120), nullable=True, index=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("masked_account_id", sa.String(64), nullable=True),
        sa.Column("canonical_symbol", sa.String(32), nullable=False, index=True),
        sa.Column("contract_id", sa.String(120), sa.ForeignKey("broker_contracts.id"), nullable=True, index=True),
        sa.Column("client_order_id", sa.String(120), nullable=False, index=True),
        sa.Column("broker_order_id", sa.String(120), nullable=True, index=True),
        sa.Column("permanent_id", sa.String(120), nullable=True, index=True),
        sa.Column("parent_order_id", sa.String(120), nullable=True, index=True),
        sa.Column("order_reference", sa.String(128), nullable=False, index=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("limit_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("stop_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("time_in_force", sa.String(16), nullable=False),
        sa.Column("internal_status", sa.String(64), nullable=False, index=True),
        sa.Column("broker_status", sa.String(64), nullable=False, index=True),
        sa.Column("filled_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_fill_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("last_fill_price", sa.Numeric(24, 10), nullable=True),
        sa.Column("commission", sa.Numeric(24, 10), nullable=True),
        sa.Column("commission_currency", sa.String(16), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(120), nullable=True),
        sa.Column("failure_message", sa.String(512), nullable=True),
        sa.Column("content_hash", sa.String(128), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("broker", "environment", "order_reference", name="uq_broker_order_reference"),
        sa.UniqueConstraint("broker", "environment", "broker_order_id", name="uq_broker_order_broker_id"),
        sa.UniqueConstraint("broker", "environment", "permanent_id", name="uq_broker_order_permanent_id"),
    )
    op.create_table(
        "broker_events",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker_order_id", sa.String(120), sa.ForeignKey("broker_orders.id"), nullable=False, index=True),
        sa.Column("event_type", sa.String(80), nullable=False, index=True),
        sa.Column("broker_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("broker_order_id", "sequence", name="uq_broker_event_order_sequence"),
    )
    op.create_table(
        "broker_executions",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker_order_id", sa.String(120), sa.ForeignKey("broker_orders.id"), nullable=False, index=True),
        sa.Column("execution_id", sa.String(120), nullable=False, index=True),
        sa.Column("broker_execution_id", sa.String(120), nullable=True, index=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("price", sa.Numeric(24, 10), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("exchange", sa.String(64), nullable=True),
        sa.Column("execution_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("commission", sa.Numeric(24, 10), nullable=True),
        sa.Column("commission_currency", sa.String(16), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(24, 10), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", name="uq_broker_execution_id"),
    )
    op.create_table(
        "broker_reconciliations",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("session_id", sa.String(120), nullable=True, index=True),
        sa.Column("trade_id", sa.String(120), nullable=True, index=True),
        sa.Column("order_id", sa.String(120), sa.ForeignKey("broker_orders.id"), nullable=True, index=True),
        sa.Column("status", sa.String(64), nullable=False, index=True),
        sa.Column("local_snapshot", sa.JSON(), nullable=False),
        sa.Column("broker_snapshot", sa.JSON(), nullable=False),
        sa.Column("differences", sa.JSON(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broker_recovery_runs",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("status", sa.String(64), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_candidates_loaded", sa.Integer(), nullable=False, default=0),
        sa.Column("active_orders_loaded", sa.Integer(), nullable=False, default=0),
        sa.Column("broker_orders_loaded", sa.Integer(), nullable=False, default=0),
        sa.Column("positions_loaded", sa.Integer(), nullable=False, default=0),
        sa.Column("mismatches_found", sa.Integer(), nullable=False, default=0),
        sa.Column("blocking_reason", sa.String(256), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broker_incidents",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("broker", sa.String(32), nullable=False, index=True),
        sa.Column("environment", sa.String(32), nullable=False, index=True),
        sa.Column("severity", sa.String(32), nullable=False, index=True),
        sa.Column("incident_type", sa.String(80), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("order_id", sa.String(120), sa.ForeignKey("broker_orders.id"), nullable=True, index=True),
        sa.Column("trade_id", sa.String(120), nullable=True, index=True),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False, default=False, index=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("broker_incidents")
    op.drop_table("broker_recovery_runs")
    op.drop_table("broker_reconciliations")
    op.drop_table("broker_executions")
    op.drop_table("broker_events")
    op.drop_table("broker_orders")
    op.drop_index("ix_broker_contract_lookup", table_name="broker_contracts")
    op.drop_table("broker_contracts")
    op.drop_table("broker_connection_sessions")

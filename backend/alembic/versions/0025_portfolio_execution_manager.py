"""portfolio execution manager

Revision ID: 0025_portfolio_execution_manager
Revises: 0024_adaptive_demo_active_management
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_portfolio_execution_manager"
down_revision = "0024_adaptive_demo_active_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_execution_snapshots",
        sa.Column("snapshot_id", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False, server_default="MT5"),
        sa.Column("account_mode", sa.String(16), nullable=False, server_default="DEMO"),
        sa.Column("balance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("equity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("free_margin", sa.Float(), nullable=False, server_default="0"),
        sa.Column("margin", sa.Float(), nullable=False, server_default="0"),
        sa.Column("margin_level", sa.Float(), nullable=True),
        sa.Column("floating_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("drawdown", sa.Float(), nullable=False, server_default="0"),
        sa.Column("open_risk", sa.Float(), nullable=False, server_default="0"),
        sa.Column("projected_stop_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("projected_take_profit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("worst_case_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_gain", sa.Float(), nullable=False, server_default="0"),
        sa.Column("margin_utilization", sa.Float(), nullable=False, server_default="0"),
        sa.Column("var_estimate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("maximum_simultaneous_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("exposure_by_currency", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("exposure_by_symbol", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("exposure_by_strategy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("exposure_by_direction", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("exposure_by_timeframe", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("exposure_by_session", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("correlation_matrix", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("position_priority", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("protection_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("portfolio_execution_snapshots", ["account_id", "broker", "account_mode", "created_at"])

    op.create_table(
        "portfolio_execution_orders",
        sa.Column("execution_id", sa.String(160), primary_key=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False, server_default="MT5"),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("requested_volume", sa.Float(), nullable=True),
        sa.Column("order_ticket", sa.String(64), nullable=True),
        sa.Column("deal_ticket", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("fill_latency_ms", sa.Float(), nullable=True),
        sa.Column("slippage", sa.Float(), nullable=True),
        sa.Column("spread_paid", sa.Float(), nullable=True),
        sa.Column("retcode", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_request", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("portfolio_execution_orders", ["idempotency_key", "source", "broker", "symbol", "action_type", "order_ticket", "deal_ticket", "state", "duplicate", "retcode", "created_at"])

    op.create_table(
        "portfolio_execution_state_transitions",
        sa.Column("transition_id", sa.String(180), primary_key=True),
        sa.Column("execution_id", sa.String(160), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=True),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("portfolio_execution_state_transitions", ["execution_id", "from_state", "to_state", "created_at"])

    op.create_table(
        "portfolio_execution_metrics",
        sa.Column("metric_id", sa.String(128), primary_key=True),
        sa.Column("window", sa.String(32), nullable=False, server_default="rolling_100"),
        sa.Column("average_latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fill_latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_slippage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_spread_paid", sa.Float(), nullable=False, server_default="0"),
        sa.Column("requotes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_fills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("broker_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("portfolio_execution_metrics", ["window", "created_at"])


def downgrade() -> None:
    op.drop_table("portfolio_execution_metrics")
    op.drop_table("portfolio_execution_state_transitions")
    op.drop_table("portfolio_execution_orders")
    op.drop_table("portfolio_execution_snapshots")


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])

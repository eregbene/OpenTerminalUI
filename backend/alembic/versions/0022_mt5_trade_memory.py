"""mt5 trade memory

Revision ID: 0022_mt5_trade_memory
Revises: 0021_decision_context
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_mt5_trade_memory"
down_revision = "0021_decision_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mt5_trade_memory_snapshots",
        sa.Column("memory_id", sa.String(128), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("market_regime", sa.String(64), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expectancy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("profit_factor", sa.Float(), nullable=True),
        sa.Column("total_realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_win", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mistake_counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("lessons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recommendation", sa.String(32), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "symbol", "session", "market_regime", name="uq_mt5_memory_scope_symbol_session_regime"),
    )
    for col in ("scope", "symbol", "session", "market_regime", "trade_count", "closed_trade_count", "recommendation", "created_at"):
        op.create_index(f"ix_mt5_trade_memory_snapshots_{col}", "mt5_trade_memory_snapshots", [col])
    op.create_index("ix_mt5_memory_lookup", "mt5_trade_memory_snapshots", ["symbol", "session", "market_regime", "recommendation"])


def downgrade() -> None:
    op.drop_index("ix_mt5_memory_lookup", table_name="mt5_trade_memory_snapshots")
    for col in ("created_at", "recommendation", "closed_trade_count", "trade_count", "market_regime", "session", "symbol", "scope"):
        op.drop_index(f"ix_mt5_trade_memory_snapshots_{col}", table_name="mt5_trade_memory_snapshots")
    op.drop_table("mt5_trade_memory_snapshots")

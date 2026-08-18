"""Strategy performance monitor: recommendations table

Revision ID: 0066_strategy_performance_recommendations
Revises: 0065_provider_reconciliation_extended_metrics
Create Date: 2026-08-18

Adds strategy_performance_recommendations -- a recurring, real-trade-driven monitor that
periodically recomputes each strategy's recent expectancy/win-rate/$P&L and proposes an
activation change when current activation disagrees with the evidence. Purely additive; this
table is DECISION SUPPORT ONLY -- nothing here ever auto-applies an activation change, see
backend/mt5_strategies/performance_monitor.py's module docstring.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066_strategy_performance_recommendations"
down_revision: Union[str, Sequence[str], None] = "0065_provider_reconciliation_extended_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "strategy_performance_recommendations"


def _create_table_if_missing(inspector: sa.Inspector, name: str, *columns: sa.Column, **kwargs) -> None:
    if name in inspector.get_table_names():
        return
    op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str], **kwargs) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _create_table_if_missing(
        inspector, _TABLE,
        sa.Column("recommendation_id", sa.String(160), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False),
        sa.Column("data_source", sa.String(24), nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("expectancy_r", sa.Float, nullable=True),
        sa.Column("realized_usd", sa.Float, nullable=True),
        sa.Column("avg_realized_usd", sa.Float, nullable=True),
        sa.Column("current_activation", sa.String(24), nullable=False),
        sa.Column("recommended_activation", sa.String(24), nullable=False),
        sa.Column("reasoning", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("reviewed_by", sa.String(120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing(inspector, "ix_spr_strategy_id", _TABLE, ["strategy_id"])
    _create_index_if_missing(inspector, "ix_spr_status", _TABLE, ["status"])
    _create_index_if_missing(inspector, "ix_spr_computed_at", _TABLE, ["computed_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)

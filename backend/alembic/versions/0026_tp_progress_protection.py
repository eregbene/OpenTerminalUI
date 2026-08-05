"""tp progress protection, stop quality audit, partial exit staging

Revision ID: 0026_tp_progress_protection
Revises: 0025_portfolio_execution_manager
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_tp_progress_protection"
down_revision = "0025_portfolio_execution_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("adaptive_position_states", sa.Column("tp_progress", sa.Float(), nullable=False, server_default="0"))
    op.add_column("adaptive_position_states", sa.Column("max_tp_progress", sa.Float(), nullable=False, server_default="0"))
    op.add_column("adaptive_position_states", sa.Column("max_tp_progress_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("current_giveback_r", sa.Float(), nullable=False, server_default="0"))
    op.add_column("adaptive_position_states", sa.Column("winner_classification", sa.String(32), nullable=False, server_default="insufficient_data"))
    op.add_column("adaptive_position_states", sa.Column("stop_quality_classification", sa.String(32), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("profit_lock_floor_r", sa.Float(), nullable=True))
    op.create_index("ix_adaptive_position_states_winner_classification", "adaptive_position_states", ["winner_classification"])
    op.create_index("ix_adaptive_position_states_stop_quality_classification", "adaptive_position_states", ["stop_quality_classification"])

    op.create_table(
        "adaptive_stop_quality_audits",
        sa.Column("audit_id", sa.String(128), primary_key=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False, server_default="UNKNOWN"),
        sa.Column("timeframe", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("sl_distance_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sl_atr_multiple", sa.Float(), nullable=True),
        sa.Column("spread_pct_of_sl", sa.Float(), nullable=True),
        sa.Column("structure_buffer_price", sa.Float(), nullable=True),
        sa.Column("broker_min_stop_price", sa.Float(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("classification", sa.String(32), nullable=False, server_default="insufficient_data"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("position_id", name="uq_adaptive_stop_quality_position"),
    )
    _indexes("adaptive_stop_quality_audits", ["position_id", "symbol", "strategy_id", "timeframe", "classification", "created_at"])

    op.create_table(
        "adaptive_partial_exit_stages",
        sa.Column("stage_id", sa.String(160), primary_key=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("trigger_tp_progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("requested_fraction", sa.Float(), nullable=False, server_default="0"),
        sa.Column("executed_volume", sa.Float(), nullable=True),
        sa.Column("action_id", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("position_id", "stage", name="uq_adaptive_partial_stage_position_stage"),
    )
    _indexes("adaptive_partial_exit_stages", ["position_id", "stage", "action_id", "created_at"])


def downgrade() -> None:
    op.drop_table("adaptive_partial_exit_stages")
    op.drop_table("adaptive_stop_quality_audits")
    op.drop_index("ix_adaptive_position_states_stop_quality_classification", table_name="adaptive_position_states")
    op.drop_index("ix_adaptive_position_states_winner_classification", table_name="adaptive_position_states")
    for column in (
        "profit_lock_floor_r",
        "stop_quality_classification",
        "winner_classification",
        "current_giveback_r",
        "max_tp_progress_at",
        "max_tp_progress",
        "tp_progress",
    ):
        op.drop_column("adaptive_position_states", column)


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])

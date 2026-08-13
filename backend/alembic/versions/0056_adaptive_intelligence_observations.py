"""historical intelligence: adaptive manager observation table

Revision ID: 0056_adaptive_intelligence_observations
Revises: 0055_historical_intelligence_observations
Create Date: 2026-08-13

Adds adaptive_intelligence_observations (Part 17/18/21) -- one row per Adaptive Trade Manager
management-cycle decision's Historical Intelligence evaluation, written by
adaptive_intelligence.py::record_observation from service.py::_monitor_cycle AFTER
_select_action/_persist_action have already finalized the real decision.

Purely additive, idempotent (existence-checked), Adaptive Trade Manager decision path unaffected.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_adaptive_intelligence_observations"
down_revision: Union[str, Sequence[str], None] = "0055_historical_intelligence_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        inspector, "adaptive_intelligence_observations",
        sa.Column("observation_id", sa.String(160), primary_key=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("cycle_run_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("reliability", sa.String(24), nullable=True),
        sa.Column("resolved_sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("probability_reach_original_tp", sa.Float, nullable=True),
        sa.Column("probability_reach_plus_1r", sa.Float, nullable=True),
        sa.Column("probability_reversal", sa.Float, nullable=True),
        sa.Column("probability_round_trip", sa.Float, nullable=True),
        sa.Column("expected_additional_r", sa.Float, nullable=True),
        sa.Column("actual_action_type", sa.String(64), nullable=False),
        sa.Column("recommended_action_type", sa.String(64), nullable=True),
        sa.Column("recommendation_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("peer_group_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("position_id", "cycle_run_id", "strategy", "symbol", "status", "peer_group_hash", "created_at"):
        _create_index_if_missing(inspector, f"ix_aio_{col}", "adaptive_intelligence_observations", [col])
    _create_index_if_missing(inspector, "ix_aio_position_time", "adaptive_intelligence_observations", ["position_id", "created_at"])


def downgrade() -> None:
    op.drop_table("adaptive_intelligence_observations")

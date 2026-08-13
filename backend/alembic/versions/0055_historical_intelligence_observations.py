"""historical intelligence: entry observation table

Revision ID: 0055_historical_intelligence_observations
Revises: 0054_historical_intelligence_fingerprints
Create Date: 2026-08-13

Adds historical_intelligence_observations (Part 21) -- one row per live candidate's Historical
Intelligence evaluation, written by entry_intelligence.py::record_observations from
autonomous.py::_record_cycle AFTER the cycle's decision is already finalized. Deliberately a
SEPARATE table from mt5_candidate_evaluations (immutable-by-design, see candidate_evaluation.py)
rather than a column added there, so this can be written by a later hook without violating that
immutability guarantee.

Purely additive, idempotent (existence-checked), live trading path unaffected.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055_historical_intelligence_observations"
down_revision: Union[str, Sequence[str], None] = "0054_historical_intelligence_fingerprints"
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
        inspector, "historical_intelligence_observations",
        sa.Column("observation_id", sa.String(160), primary_key=True),
        sa.Column("evaluation_id", sa.String(128), nullable=False),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("trust_state", sa.String(32), nullable=True),
        sa.Column("reliability", sa.String(24), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expectancy_r", sa.Float, nullable=True),
        sa.Column("profit_factor", sa.Float, nullable=True),
        sa.Column("immediate_failure_probability", sa.Float, nullable=True),
        sa.Column("probability_0_5r", sa.Float, nullable=True),
        sa.Column("probability_1r", sa.Float, nullable=True),
        sa.Column("probability_1_5r", sa.Float, nullable=True),
        sa.Column("probability_2r", sa.Float, nullable=True),
        sa.Column("historical_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("ranking_adjustment", sa.Float, nullable=False, server_default="0"),
        sa.Column("ranking_adjustment_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("defer_reject_reason", sa.String(64), nullable=True),
        sa.Column("peer_group_hash", sa.String(64), nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_id", name="uq_hio_evaluation"),
    )
    for col in ("evaluation_id", "cycle_id", "strategy_id", "status", "peer_group_hash", "created_at"):
        _create_index_if_missing(inspector, f"ix_hio_{col}", "historical_intelligence_observations", [col])


def downgrade() -> None:
    op.drop_table("historical_intelligence_observations")

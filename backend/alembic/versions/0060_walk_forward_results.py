"""historical intelligence: walk-forward / OOS validation results

Revision ID: 0060_walk_forward_results
Revises: 0059_observation_evaluation_source
Create Date: 2026-08-13

Adds historical_walk_forward_results (Phase 1 of the Forex/MT5 roadmap) -- chronological
train/OOS split statistics + edge_stability classification per strategy/symbol/regime/session/
confidence-band/peer-group, computed over the REAL replayed MT5 strategy outcome corpus (never
the old DSL/equities backtester). Purely additive, idempotent.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060_walk_forward_results"
down_revision: Union[str, Sequence[str], None] = "0059_observation_evaluation_source"
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
        inspector, "historical_walk_forward_results",
        sa.Column("result_id", sa.String(160), primary_key=True),
        sa.Column("anchor_strategy", sa.String(64), nullable=True),
        sa.Column("canonical_symbol", sa.String(32), nullable=True),
        sa.Column("regime", sa.String(64), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("confidence_band", sa.String(32), nullable=True),
        sa.Column("peer_group_hash", sa.String(64), nullable=True),
        sa.Column("edge_stability", sa.String(24), nullable=False),
        sa.Column("total_n", sa.Integer, nullable=False, server_default="0"),
        sa.Column("train_n", sa.Integer, nullable=False, server_default="0"),
        sa.Column("oos_n", sa.Integer, nullable=False, server_default="0"),
        sa.Column("train_expectancy_r", sa.Float, nullable=True),
        sa.Column("oos_expectancy_r", sa.Float, nullable=True),
        sa.Column("degradation_pct", sa.Float, nullable=True),
        sa.Column("train_stats", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("oos_stats", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("anchor_strategy", "canonical_symbol", "peer_group_hash", "edge_stability", "computed_at"):
        _create_index_if_missing(inspector, f"ix_hwfr_{col}", "historical_walk_forward_results", [col])
    _create_index_if_missing(inspector, "ix_hwfr_strategy_computed", "historical_walk_forward_results", ["anchor_strategy", "computed_at"])


def downgrade() -> None:
    op.drop_table("historical_walk_forward_results")

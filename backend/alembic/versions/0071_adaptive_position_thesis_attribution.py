"""adaptive_position_states: setup subtype + original target/SL type + original confidence

Revision ID: 0071_adaptive_position_thesis_attribution
Revises: 0070_candidate_evaluation_historical_intelligence
Create Date: 2026-08-25

Bensim -- Adaptive Manager V3, Part 8 ("the manager should not have to infer the original
thesis after the position is already open"). strategy_id/entry_regime/entry_atr etc already
exist on this table; this adds the four pieces of "why this trade exists" the manager's
strategy-aware policy profiles (backend/adaptive_management/strategy_profiles.py) actually
consume: setup_subtype (e.g. "EMA_PULLBACK", "BOS_DISPLACEMENT_CONTINUATION", "FVG_OB"),
original_target_type/original_sl_type (the geometry basis strings already computed at entry --
e.g. mtfai1_v2_fvg, atr_projected_move, structural_swing), original_confidence (the overall
confidence score at entry time, distinct from the position's CURRENT winner_classification).

Purely additive: nullable columns, existing rows simply have no thesis record (honest --
predate this column, not backfilled/fabricated).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071_adaptive_position_thesis_attribution"
down_revision: Union[str, Sequence[str], None] = "0070_candidate_evaluation_historical_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "adaptive_position_states"


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name in existing:
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_column_if_missing(inspector, _TABLE, sa.Column("setup_subtype", sa.String(64), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("original_target_type", sa.String(64), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("original_sl_type", sa.String(64), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("original_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "original_confidence")
    op.drop_column(_TABLE, "original_sl_type")
    op.drop_column(_TABLE, "original_target_type")
    op.drop_column(_TABLE, "setup_subtype")

"""adaptive_position_states: original structural reference price (breakout-level invalidation)

Revision ID: 0072_adaptive_position_structural_reference
Revises: 0071_adaptive_position_thesis_attribution
Create Date: 2026-08-25

Bensim -- Adaptive Manager V3 continuation, Part 5/6 (session_breakout/breakout structural-
reacceptance invalidation): the raw price of the level a breakout-family entry referenced
(broken session high/low, broken BOS level, etc.) is already computed at entry time
(_shared.py::_geometry_metadata's "structural_reference" field, persisted into
strategy_evidence.stop_geometry on the real candidate row) but was not yet captured onto the
live-managed position -- this column closes that gap so the manager can compare CURRENT price
against the ORIGINAL broken level without re-detecting structure.

Purely additive: nullable column, existing rows simply have no reference (honest -- predates
this column, not fabricated).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0072_adaptive_position_structural_reference"
down_revision: Union[str, Sequence[str], None] = "0071_adaptive_position_thesis_attribution"
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
    _add_column_if_missing(inspector, _TABLE, sa.Column("original_structural_reference", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "original_structural_reference")

"""Walk-forward results: directional-edge taxonomy column

Revision ID: 0064_walk_forward_directional_edge
Revises: 0063_adaptive_intelligence_observation_source
Create Date: 2026-08-14

Adds directional_edge to historical_walk_forward_results -- a sign-agreement classification
(POSITIVE_EDGE/NEGATIVE_EDGE/NEUTRAL_EDGE/UNSTABLE/INSUFFICIENT) separate from the existing
edge_stability retention-fraction classification (STRONG/ACCEPTABLE/DEGRADED/FAILED_OOS/
INSUFFICIENT_SAMPLE), so a reliably, reproducibly NEGATIVE strategy can be distinguished from a
genuinely unstable/no-signal one. Purely additive.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064_walk_forward_directional_edge"
down_revision: Union[str, Sequence[str], None] = "0063_adaptive_intelligence_observation_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "historical_walk_forward_results"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    if "directional_edge" not in existing:
        op.add_column(_TABLE, sa.Column("directional_edge", sa.String(24), nullable=True))
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    if "ix_historical_walk_forward_results_directional_edge" not in existing_indexes:
        op.create_index("ix_historical_walk_forward_results_directional_edge", _TABLE, ["directional_edge"])


def downgrade() -> None:
    op.drop_index("ix_historical_walk_forward_results_directional_edge", table_name=_TABLE)
    op.drop_column(_TABLE, "directional_edge")

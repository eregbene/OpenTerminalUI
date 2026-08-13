"""Adaptive intelligence observations: evidence-source observability columns

Revision ID: 0063_adaptive_intelligence_observation_source
Revises: 0062_historical_adaptive_states
Create Date: 2026-08-13

Adds evaluation_source/raw_neighbor_count/independent_neighbor_count/lookup_latency_ms to
adaptive_intelligence_observations -- lets a real observation record show whether it was
evaluated via the EXACT peer-group match or the SIMILARITY_WEIGHTED multi-neighbor fallback (see
adaptive_intelligence.py's dual-source evaluate_adaptive_intelligence), and how much evidence/
latency that lookup involved. Purely additive.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063_adaptive_intelligence_observation_source"
down_revision: Union[str, Sequence[str], None] = "0062_historical_adaptive_states"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "adaptive_intelligence_observations"
_COLUMNS = [
    ("evaluation_source", sa.String(24)),
    ("raw_neighbor_count", sa.Integer),
    ("independent_neighbor_count", sa.Integer),
    ("lookup_latency_ms", sa.Float),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    for name, col_type in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, col_type, nullable=True))
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    if "ix_aio_evaluation_source" not in existing_indexes:
        op.create_index("ix_aio_evaluation_source", _TABLE, ["evaluation_source"])


def downgrade() -> None:
    op.drop_index("ix_aio_evaluation_source", table_name=_TABLE)
    for name, _ in _COLUMNS:
        op.drop_column(_TABLE, name)

"""historical_intelligence_observations: add evaluation_source

Revision ID: 0059_observation_evaluation_source
Revises: 0058_fingerprint_regime_broad
Create Date: 2026-08-13

Adds `evaluation_source` (EXACT_PEER_GROUP | SIMILARITY_WEIGHTED) -- which evidence lens actually
produced an observation's statistics, now that entry_intelligence.py can fall back to
similarity.py's weighted effective-sample-size view when the exact peer-group match doesn't
independently qualify (Workstream 9). Never hidden inside the score.

Purely additive, idempotent.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_observation_evaluation_source"
down_revision: Union[str, Sequence[str], None] = "0058_fingerprint_regime_broad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name in existing:
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_column_if_missing(inspector, "historical_intelligence_observations", sa.Column("evaluation_source", sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column("historical_intelligence_observations", "evaluation_source")

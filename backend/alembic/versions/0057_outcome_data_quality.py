"""historical_setup_outcomes: add data_quality tier

Revision ID: 0057_outcome_data_quality
Revises: 0056_adaptive_intelligence_observations
Create Date: 2026-08-13

Adds `data_quality` (HIGH|ACCEPTABLE|APPROXIMATE|UNTRUSTED) to historical_setup_outcomes --
outcome labeling now reads future candles through the same revision-aware, finalized-bar-aware
source hierarchy as point-in-time replay (see outcomes.py's rewritten module docstring), rather
than reading mt5_canonical_candles directly with no quality tiering. UNTRUSTED outcomes are still
persisted for audit but are excluded from every active statistic in statistics.py.

Purely additive, idempotent, no existing data touched (existing rows default to 'UNTRUSTED' --
conservative: they predate this fix and were computed via the old, non-revision-aware path, so
their exact provenance can no longer be reconstructed; they are correctly excluded from active
statistics until re-labeled).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_outcome_data_quality"
down_revision: Union[str, Sequence[str], None] = "0056_adaptive_intelligence_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name in existing:
        return
    op.add_column(table_name, column)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str], **kwargs) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_column_if_missing(inspector, "historical_setup_outcomes", sa.Column("data_quality", sa.String(16), nullable=False, server_default="UNTRUSTED"))
    _create_index_if_missing(inspector, "ix_hso_data_quality", "historical_setup_outcomes", ["data_quality"])


def downgrade() -> None:
    op.drop_index("ix_hso_data_quality", table_name="historical_setup_outcomes")
    op.drop_column("historical_setup_outcomes", "data_quality")

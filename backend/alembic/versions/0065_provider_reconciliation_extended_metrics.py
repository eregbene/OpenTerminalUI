"""Provider reconciliation: extended overlap-quality metrics

Revision ID: 0065_provider_reconciliation_extended_metrics
Revises: 0064_walk_forward_directional_edge
Create Date: 2026-08-15

Adds p95_relative_diff, missing_bar_rate, timestamp_alignment_rate,
ohlc_consistency_median_diff to historical_provider_reconciliations -- additive diagnostics
computed by quality.compare_provider_overlap() alongside the existing median/mean/max
relative_diff fields. `status` (NO_OVERLAP/NO_COMPARABLE_PRICES/CONSISTENT/DIVERGENT/
INCOMPATIBLE) continues to be derived from median/max_relative_diff alone, unchanged --
these four columns are reporting-only, never part of the classification. Purely additive.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065_provider_reconciliation_extended_metrics"
down_revision: Union[str, Sequence[str], None] = "0064_walk_forward_directional_edge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "historical_provider_reconciliations"
_NEW_COLUMNS = ("p95_relative_diff", "missing_bar_rate", "timestamp_alignment_rate", "ohlc_consistency_median_diff")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)} if _TABLE in inspector.get_table_names() else set()
    for column_name in _NEW_COLUMNS:
        if column_name not in existing:
            op.add_column(_TABLE, sa.Column(column_name, sa.Float(), nullable=True))


def downgrade() -> None:
    for column_name in reversed(_NEW_COLUMNS):
        op.drop_column(_TABLE, column_name)

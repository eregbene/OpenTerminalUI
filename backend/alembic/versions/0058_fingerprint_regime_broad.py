"""historical_pattern_fingerprints: add regime_broad (fp-v2 peer-grouping)

Revision ID: 0058_fingerprint_regime_broad
Revises: 0057_outcome_data_quality
Create Date: 2026-08-13

Adds `regime_broad` -- the coarse TRENDING/BREAKOUT/REVERSAL/RANGING/UNKNOWN bucket now used by
peer_group_hash (fp-v2), replacing the previous 18-dimension hash that produced 549 peer groups
from 1,972 fingerprints (avg 3.6/group -- too fragmented for any statistic to reach a usable
sample size). See fingerprint.py's module docstring for the full rationale.

Purely additive, idempotent, no existing data destroyed -- fp-v1 rows keep their old
peer_group_hash values and remain queryable under fingerprint_version='fp-v1'; regenerating a
fingerprint (pattern_builder.py, upsert-by-evaluation_id) naturally supersedes it to fp-v2.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0058_fingerprint_regime_broad"
down_revision: Union[str, Sequence[str], None] = "0057_outcome_data_quality"
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
    _add_column_if_missing(inspector, "historical_pattern_fingerprints", sa.Column("regime_broad", sa.String(16), nullable=True))
    _create_index_if_missing(inspector, "ix_hpf_regime_broad", "historical_pattern_fingerprints", ["regime_broad"])


def downgrade() -> None:
    op.drop_index("ix_hpf_regime_broad", table_name="historical_pattern_fingerprints")
    op.drop_column("historical_pattern_fingerprints", "regime_broad")

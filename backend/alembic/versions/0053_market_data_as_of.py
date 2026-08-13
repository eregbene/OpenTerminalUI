"""mt5_candidate_evaluations: persist the exact market-data-fetch instant

Revision ID: 0053_market_data_as_of
Revises: 0052_historical_intelligence_point_in_time_integrity
Create Date: 2026-08-13

Adds `market_data_as_of` (nullable, additive) -- the exact timestamp of the last M15 bar used to
build a candidate's StrategyContext (already computed live as context["timestamp"] in
autonomous.py::_screen, but previously discarded after use, never persisted). Historical
Intelligence's reconstructed-tier replay previously anchored on `created_at` (the cycle's
post-decision DB-write time), which lags the real market-data-fetch instant by however long
prefilter/scoring/confidence-calibration/DB-write took for potentially many other symbols in the
same cycle -- identified as a likely source of residual replay imprecision for
threshold-sensitive strategies (EMA100 alignment, non-session-anchored cumulative VWAP, short
structural-recency windows) while investigating weak reconstructed-tier parity for several
strategy families. Rows written before this column existed simply have it NULL; replay/parity
fall back to created_at for those.

Purely additive -- no existing column changes, live trading path unaffected.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053_market_data_as_of"
down_revision: Union[str, Sequence[str], None] = "0052_historical_intelligence_point_in_time_integrity"
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
    _add_column_if_missing(inspector, "mt5_candidate_evaluations", sa.Column("market_data_as_of", sa.DateTime(timezone=True), nullable=True))
    _create_index_if_missing(inspector, "ix_mt5_candidate_evaluations_market_data_as_of", "mt5_candidate_evaluations", ["market_data_as_of"])


def downgrade() -> None:
    op.drop_index("ix_mt5_candidate_evaluations_market_data_as_of", table_name="mt5_candidate_evaluations")
    op.drop_column("mt5_candidate_evaluations", "market_data_as_of")

"""mt5_candidate_evaluations: add broker_provider (multi-broker Phase 1)

Revision ID: 0067_candidate_evaluation_broker_provider
Revises: 0066_strategy_performance_recommendations
Create Date: 2026-08-22

Broker Independence Assessment Phase 1, item 1: `mt5_candidate_evaluations` was the one table in
the confidence/candidate-evaluation path with no broker/provider discriminator at all (only
`account_id`, a Bensim-internal profile id like "demo_10k" -- implicitly MT5-only). This blocked
five downstream files (pattern_builder.py, parity.py, mt5_strategies/analytics.py,
event_capture.py, performance_monitor.py) from being able to tell which broker a given evaluation
came from, unlike the candle-storage tables (mt5_canonical_candles / mt5_candle_revisions), which
already carry a real `provider` column and already ingest more than one provider today.

Purely additive: NOT NULL with server_default='MT5' backfills every existing row correctly (every
row written before this migration genuinely was MT5 -- no other provider has ever written to this
table), so no data is ambiguous or lost. New rows must pass broker_provider explicitly going
forward (backend/mt5_strategies/models.py::StrategySignal / wherever this table is populated) --
"MT5" stays the code-level default until a second broker's candidates start flowing through this
same table, matching BROKER_PROVIDER's existing "MT5" default elsewhere in the codebase.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067_candidate_evaluation_broker_provider"
down_revision: Union[str, Sequence[str], None] = "0066_strategy_performance_recommendations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mt5_candidate_evaluations"


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
    _add_column_if_missing(
        inspector, _TABLE,
        sa.Column("broker_provider", sa.String(16), nullable=False, server_default="MT5"),
    )
    _create_index_if_missing(inspector, "ix_mce_broker_provider", _TABLE, ["broker_provider"])


def downgrade() -> None:
    op.drop_index("ix_mce_broker_provider", table_name=_TABLE)
    op.drop_column(_TABLE, "broker_provider")

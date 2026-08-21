"""historical_setup_outcomes: add execution-cost provenance (gross-vs-net realism, Phase 1)

Revision ID: 0068_execution_cost_provenance
Revises: 0067_candidate_evaluation_broker_provider
Create Date: 2026-08-21

QuantConnect/LEAN gap-analysis roadmap Phase 1, item 1: outcomes.py's `net_outcome_r` previously
had no persisted memory of WHICH spread value (if any) produced it, and no field distinguishing
"a real spread was known" from "no cost could be honestly determined" -- both cases silently
looked identical (net_outcome_r = None) to any caller. This migration adds explicit provenance so
every historical result can answer "where did your cost assumption come from":

  - real_spread_price: the actual spread (price units) used for this outcome's spread deduction,
    persisted ONLY when it was a genuine OBSERVED value (a real decision-snapshot bid/ask spread
    captured at or near this setup's own entry_time) -- never an estimate. Persisting only real
    observations here (not estimates) is what lets a NEW point-in-time index of genuine spread
    observations grow over time and serve HISTORICAL_ESTIMATE lookups for other setups, without
    ever contaminating that index with a previous estimate presented as if it were real.
  - spread_cost_r / spread_cost_provenance: the spread cost actually deducted (in R units) and its
    tier -- OBSERVED | HISTORICAL_ESTIMATE | CONFIG_FALLBACK | UNKNOWN (see
    execution_costs.py's module docstring for the exact definition of each tier).
  - commission_cost_r / commission_cost_provenance: same idea for commission -- this account's real
    broker-reported commission is documented as genuinely $0 (see brokers/mt5/trading_costs.py's
    own audit note), so most rows are expected to land on a CONFIG_ZERO/OBSERVED_ZERO tier rather
    than UNKNOWN, without needing any lot-size assumption for a purely hypothetical historical
    fingerprint.

Purely additive. `spread_cost_provenance`/`commission_cost_provenance` default to 'UNKNOWN' via
server_default, which is the historically-correct backfill value for every row written before this
migration -- none of them had any cost provenance tracked at all. `net_outcome_r` itself is
unchanged by this migration (existing column, existing semantics); only the NEW columns explain
where its inputs came from going forward.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068_execution_cost_provenance"
down_revision: Union[str, Sequence[str], None] = "0067_candidate_evaluation_broker_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "historical_setup_outcomes"


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
    _add_column_if_missing(inspector, _TABLE, sa.Column("real_spread_price", sa.Float(), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("spread_cost_r", sa.Float(), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("spread_cost_provenance", sa.String(24), nullable=False, server_default="UNKNOWN"))
    _add_column_if_missing(inspector, _TABLE, sa.Column("commission_cost_r", sa.Float(), nullable=True))
    _add_column_if_missing(inspector, _TABLE, sa.Column("commission_cost_provenance", sa.String(24), nullable=False, server_default="UNKNOWN"))
    _create_index_if_missing(inspector, "ix_hso_spread_cost_provenance", _TABLE, ["spread_cost_provenance"])


def downgrade() -> None:
    op.drop_index("ix_hso_spread_cost_provenance", table_name=_TABLE)
    op.drop_column(_TABLE, "commission_cost_provenance")
    op.drop_column(_TABLE, "commission_cost_r")
    op.drop_column(_TABLE, "spread_cost_provenance")
    op.drop_column(_TABLE, "spread_cost_r")
    op.drop_column(_TABLE, "real_spread_price")

"""add gross/net trading-cost accounting columns

Revision ID: 0043_trading_cost_accounting
Revises: 0042_mtfai1_confirmation_gate
Create Date: 2026-08-12

Additive only. Introduces the canonical gross/commission/swap/fee/net cost breakdown
(backend/brokers/mt5/trading_costs.py) to two existing tables:

- mt5_trade_records: `realized_pnl` already meant net P&L in intent (see persistence.py); this
  adds `gross_pnl`, `fee` (the table had commission/swap but no fee column), and the derived
  `total_trading_cost`/`commission_per_lot_effective`/`commission_source` fields so the trade
  journal can show the full breakdown without recomputing it from raw deals on every read.
- mt5_candidate_evaluations: previously had `realized_pnl` under "outcome tracking" but NO
  commission/swap/fee columns at all, so confidence/calibration reporting was always
  pre-cost. Adds the same breakdown so confidence-band expectancy can be evaluated net of
  cost (per this feature's "is confidence 75-79 actually profitable after costs" requirement).

Does not touch any trading-decision path, threshold, or existing column's meaning.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_trading_cost_accounting"
down_revision: Union[str, Sequence[str], None] = "0042_mtfai1_confirmation_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mt5_trade_records", sa.Column("gross_pnl", sa.Float(), nullable=True))
    op.add_column("mt5_trade_records", sa.Column("fee", sa.Float(), nullable=True))
    op.add_column("mt5_trade_records", sa.Column("total_trading_cost", sa.Float(), nullable=True))
    op.add_column("mt5_trade_records", sa.Column("commission_per_lot_effective", sa.Float(), nullable=True))
    op.add_column("mt5_trade_records", sa.Column("commission_source", sa.String(24), nullable=True))

    op.add_column("mt5_candidate_evaluations", sa.Column("gross_pnl", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("commission", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("swap", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("fee", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("net_pnl", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("total_trading_cost", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("commission_source", sa.String(24), nullable=True))
    op.create_index("ix_mt5_cand_eval_net_pnl", "mt5_candidate_evaluations", ["net_pnl"])


def downgrade() -> None:
    op.drop_index("ix_mt5_cand_eval_net_pnl", table_name="mt5_candidate_evaluations")
    op.drop_column("mt5_candidate_evaluations", "commission_source")
    op.drop_column("mt5_candidate_evaluations", "total_trading_cost")
    op.drop_column("mt5_candidate_evaluations", "net_pnl")
    op.drop_column("mt5_candidate_evaluations", "fee")
    op.drop_column("mt5_candidate_evaluations", "swap")
    op.drop_column("mt5_candidate_evaluations", "commission")
    op.drop_column("mt5_candidate_evaluations", "gross_pnl")

    op.drop_column("mt5_trade_records", "commission_source")
    op.drop_column("mt5_trade_records", "commission_per_lot_effective")
    op.drop_column("mt5_trade_records", "total_trading_cost")
    op.drop_column("mt5_trade_records", "fee")
    op.drop_column("mt5_trade_records", "gross_pnl")

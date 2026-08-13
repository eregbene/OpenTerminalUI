"""account-scope MT5 autonomous operational records

Revision ID: 0045_mt5_autonomous_account_scope
Revises: 0044_portfolio_execution_account_scope
Create Date: 2026-08-12

Add logical account_id to MT5 autonomous cycle/candidate/decision/order/trade/evaluation
tables. Existing historical rows belong to the original demo_10k scheduler.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_mt5_autonomous_account_scope"
down_revision: Union[str, Sequence[str], None] = "0044_portfolio_execution_account_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = (
    "mt5_scheduler_cycles",
    "mt5_scheduler_candidates",
    "mt5_ai_decisions",
    "mt5_order_records",
    "mt5_trade_records",
    "mt5_candidate_evaluations",
)


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("account_id", sa.String(64), nullable=True))
        op.execute(f"UPDATE {table} SET account_id = 'demo_10k' WHERE account_id IS NULL")
        op.alter_column(table, "account_id", nullable=False)
        op.create_index(f"ix_{table}_account_id", table, ["account_id"])
    op.create_index(
        "ix_mt5_cand_eval_account_cycle_rank",
        "mt5_candidate_evaluations",
        ["account_id", "cycle_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index("ix_mt5_cand_eval_account_cycle_rank", table_name="mt5_candidate_evaluations")
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_column(table, "account_id")

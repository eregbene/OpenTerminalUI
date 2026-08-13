"""account-scope MT5 execution journal

Revision ID: 0044_portfolio_execution_account_scope
Revises: 0043_trading_cost_accounting
Create Date: 2026-08-12

Additive/backfill migration for multi-account MT5 execution isolation. Historical rows are
kept as the existing demo_10k account, then uniqueness moves from idempotency_key alone to
(account_id, idempotency_key) so two isolated MT5 accounts can safely use the same natural key.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_portfolio_execution_account_scope"
down_revision: Union[str, Sequence[str], None] = "0043_trading_cost_accounting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("portfolio_execution_orders", sa.Column("account_id", sa.String(64), nullable=True))
    op.execute("UPDATE portfolio_execution_orders SET account_id = 'demo_10k' WHERE account_id IS NULL")
    op.alter_column("portfolio_execution_orders", "account_id", nullable=False)
    op.create_index("ix_portfolio_execution_orders_account_id", "portfolio_execution_orders", ["account_id"])
    op.drop_constraint("portfolio_execution_orders_idempotency_key_key", "portfolio_execution_orders", type_="unique")
    op.create_unique_constraint(
        "uq_portfolio_execution_orders_account_idempotency",
        "portfolio_execution_orders",
        ["account_id", "idempotency_key"],
    )

    op.add_column("portfolio_execution_state_transitions", sa.Column("account_id", sa.String(64), nullable=True))
    op.execute("UPDATE portfolio_execution_state_transitions SET account_id = 'demo_10k' WHERE account_id IS NULL")
    op.alter_column("portfolio_execution_state_transitions", "account_id", nullable=False)
    op.create_index("ix_portfolio_execution_state_transitions_account_id", "portfolio_execution_state_transitions", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_execution_state_transitions_account_id", table_name="portfolio_execution_state_transitions")
    op.drop_column("portfolio_execution_state_transitions", "account_id")

    op.drop_constraint("uq_portfolio_execution_orders_account_idempotency", "portfolio_execution_orders", type_="unique")
    op.create_unique_constraint("portfolio_execution_orders_idempotency_key_key", "portfolio_execution_orders", ["idempotency_key"])
    op.drop_index("ix_portfolio_execution_orders_account_id", table_name="portfolio_execution_orders")
    op.drop_column("portfolio_execution_orders", "account_id")

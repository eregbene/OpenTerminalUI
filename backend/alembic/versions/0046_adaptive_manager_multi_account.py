"""account-scope the Adaptive Trade Manager for genuine multi-account isolation

Revision ID: 0046_adaptive_manager_multi_account
Revises: 0045_mt5_autonomous_account_scope
Create Date: 2026-08-12

The MT5 order/candidate engine already has real multi-account support (multi_account.py,
account_registry.py, migrations 0044/0045). The Adaptive Trade Manager (adaptive_management/
service.py + analytics.py) did not: it read the single global mt5_adapter/mt5_config()
everywhere, AdaptivePositionStateORM.position_id (the raw MT5 broker ticket, reused across the
whole module as a bare primary key) had no account prefix so two accounts with colliding ticket
numbers would silently overwrite each other's row, and several safety-relevant singletons
(_active_activation's query, the circuit breaker row) had no account scoping at all -- meaning
one account's activation/circuit-breaker state could leak into another's.

This migration adds `account_id` (matching the MT5-side tables' existing convention, not the
adaptive-management side's pre-existing but under-used `account_fingerprint`) to every table
that lacked ANY account scoping, and backfills all historical rows to 'demo_10k' -- the only
account with real data before this feature. `adaptive_trade_sessions.account_id` already existed
(nullable, unused) -- this migration backfills and makes it non-null for consistency, it does
not re-add the column.

Does not touch any trading-decision path, threshold, or existing column's meaning.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046_adaptive_manager_multi_account"
down_revision: Union[str, Sequence[str], None] = "0045_mt5_autonomous_account_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMN_TABLES = (
    "adaptive_trade_events",
    "adaptive_management_activations",
    "adaptive_position_states",
    "adaptive_stop_quality_audits",
    "adaptive_partial_exit_stages",
    "adaptive_partial_profit_stages",
    "adaptive_management_actions",
    "adaptive_circuit_breakers",
    "adaptive_position_adoptions",
    "adaptive_management_events",
    "adaptive_position_baselines",
    "adaptive_manager_counterfactuals",
    "mt5_trade_memory_snapshots",
)


def upgrade() -> None:
    for table in NEW_COLUMN_TABLES:
        op.add_column(table, sa.Column("account_id", sa.String(64), nullable=True))
        op.execute(f"UPDATE {table} SET account_id = 'demo_10k' WHERE account_id IS NULL")
        op.alter_column(table, "account_id", nullable=False)
        op.create_index(f"ix_{table}_account_id", table, ["account_id"])

    # adaptive_trade_sessions.account_id already existed, nullable, unused -- backfill + enforce.
    op.execute("UPDATE adaptive_trade_sessions SET account_id = 'demo_10k' WHERE account_id IS NULL OR account_id = ''")
    op.alter_column("adaptive_trade_sessions", "account_id", nullable=False, server_default="demo_10k")


def downgrade() -> None:
    op.alter_column("adaptive_trade_sessions", "account_id", nullable=True, server_default=None)

    for table in reversed(NEW_COLUMN_TABLES):
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_column(table, "account_id")

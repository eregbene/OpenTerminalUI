"""mt5_trade_memory_snapshots: make the uniqueness constraint account-scoped

Revision ID: 0074_mt5_memory_snapshot_account_scoped_unique
Revises: 0073_mt5_demo_safety_circuit
Create Date: 2026-08-26

Bensim -- real bug found while investigating "why do trades show as permanently open with zero
realized P&L" (backend/brokers/mt5/trade_reconciliation_monitor.py): mt5_trade_memory_snapshots'
uq_mt5_memory_scope_symbol_session_regime constraint was declared on (scope, symbol, session,
market_regime) only -- omitting account_id, even though memory_id (the real primary key) and
every read/write path (_refresh_memory_snapshots, called from update_trade_history) are already
fully account-scoped. Confirmed live: the first account to ever write a (scope, symbol, session,
regime) snapshot permanently blocked every OTHER account from ever writing that exact
combination (a genuine psycopg.errors.UniqueViolation on INSERT), silently swallowed by the
caller's broad try/except -- so 3 of the 4 DEMO accounts' memory-snapshot refresh (and therefore
the whole reconciliation() call, since update_trade_history commits everything in one
transaction) failed on every single invocation, which is the real reason those accounts'
mt5_trade_records rows never got close_timestamp/realized_pnl written even though the positions
had genuinely closed on the broker.

Purely additive/corrective: drops the old constraint, adds the correct account-scoped one. No
data loss -- existing rows keep their real, already-correct memory_id and account_id values.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0074_mt5_memory_snapshot_account_scoped_unique"
down_revision: Union[str, Sequence[str], None] = "0073_mt5_demo_safety_circuit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mt5_trade_memory_snapshots"
_OLD_CONSTRAINT = "uq_mt5_memory_scope_symbol_session_regime"
_NEW_CONSTRAINT = "uq_mt5_memory_account_scope_symbol_session_regime"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_constraints = {c["name"] for c in inspector.get_unique_constraints(_TABLE)}
    if _OLD_CONSTRAINT in existing_constraints:
        op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")
    if _NEW_CONSTRAINT not in existing_constraints:
        op.create_unique_constraint(_NEW_CONSTRAINT, _TABLE, ["account_id", "scope", "symbol", "session", "market_regime"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_constraints = {c["name"] for c in inspector.get_unique_constraints(_TABLE)}
    if _NEW_CONSTRAINT in existing_constraints:
        op.drop_constraint(_NEW_CONSTRAINT, _TABLE, type_="unique")
    if _OLD_CONSTRAINT not in existing_constraints:
        op.create_unique_constraint(_OLD_CONSTRAINT, _TABLE, ["scope", "symbol", "session", "market_regime"])

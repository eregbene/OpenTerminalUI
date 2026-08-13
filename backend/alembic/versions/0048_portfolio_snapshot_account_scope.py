"""fix portfolio snapshot account scope: canonical account_id, raw login as metadata

Revision ID: 0048_portfolio_snapshot_account_scope
Revises: 0047_mt5_prop_daily_state
Create Date: 2026-08-12

Confirmed bug (multi-account risk/protection audit, Bug 1 -- HIGHEST PRIORITY): build_snapshot()
persisted portfolio_execution_snapshots.account_id as the raw MT5 broker login number (e.g.
"5054067375"), while every read (latest_snapshot/exposure/can_open_new_trade, called from
autonomous.py/candidate_evaluation.py) filters by the canonical profile id
(demo_10k/ftmo_demo_25k/ftmo_demo_50k/ftmo_demo_100k). The lookup never matched, so
can_open_new_trade() always fell through to its "no snapshot found" branch, which (a separate,
also-fixed bug) returned (True, []) -- portfolio exposure/correlation/margin/aggregate-risk
protection failed open for every account, including demo_10k itself.

This migration adds `mt5_login` (raw broker login, metadata only, never a lookup key) and
backfills the historical data. Every existing row (13,426 as of this writing, verified via
direct query) has account_id == '5054067375', which was independently confirmed live to be
demo_10k's real, current MT5 account login -- an unambiguous 1:1 mapping, not a guess. Rows are
therefore backfilled to mt5_login='5054067375', account_id='demo_10k'. If a different login value
is ever found in this column (it should not be, per the query above), it is left untouched rather
than guessed at.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_portfolio_snapshot_account_scope"
down_revision: Union[str, Sequence[str], None] = "0047_mt5_prop_daily_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KNOWN_DEMO_10K_LOGIN = "5054067375"


def upgrade() -> None:
    op.add_column("portfolio_execution_snapshots", sa.Column("mt5_login", sa.String(32), nullable=True))
    op.create_index("ix_portfolio_execution_snapshots_mt5_login", "portfolio_execution_snapshots", ["mt5_login"])
    op.execute(
        f"""
        UPDATE portfolio_execution_snapshots
        SET mt5_login = '{KNOWN_DEMO_10K_LOGIN}', account_id = 'demo_10k'
        WHERE account_id = '{KNOWN_DEMO_10K_LOGIN}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE portfolio_execution_snapshots
        SET account_id = '{KNOWN_DEMO_10K_LOGIN}'
        WHERE account_id = 'demo_10k' AND mt5_login = '{KNOWN_DEMO_10K_LOGIN}'
        """
    )
    op.drop_index("ix_portfolio_execution_snapshots_mt5_login", table_name="portfolio_execution_snapshots")
    op.drop_column("portfolio_execution_snapshots", "mt5_login")

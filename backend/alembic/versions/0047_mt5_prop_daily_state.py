"""add per-account daily-loss baseline state for prop protection

Revision ID: 0047_mt5_prop_daily_state
Revises: 0046_adaptive_manager_multi_account
Create Date: 2026-08-12

Confirmed bug (multi-account risk/protection audit, Bug 3): no persistent per-account daily-loss
baseline existed. backend/api/routes/brokers.py's two challenge_status() call sites both passed
daily_baseline_equity=profile.expected_initial_balance -- the account's original starting
capital, never a true daily reset -- so daily-loss utilization was effectively measured against
lifetime state instead of "today". This migration adds mt5_prop_daily_state, a write-once
per-(account_id, trading_day) row capturing day_start_balance/day_start_equity the first time an
account is observed on a given trading day, computed in that account's own
PropRiskProfile.daily_reset_timezone (see backend/brokers/mt5/prop_state.py). No historical
backfill is performed: there is no way to know what any account's true equity was at each past
day's reset boundary, so the table starts empty and rows are captured going forward only.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_mt5_prop_daily_state"
down_revision: Union[str, Sequence[str], None] = "0046_adaptive_manager_multi_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_prop_daily_state",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("trading_day", sa.String(10), primary_key=True),
        sa.Column("reset_timezone", sa.String(64), nullable=False),
        sa.Column("day_start_balance", sa.Float, nullable=False),
        sa.Column("day_start_equity", sa.Float, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt5_prop_daily_state_created_at", "mt5_prop_daily_state", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mt5_prop_daily_state_created_at", table_name="mt5_prop_daily_state")
    op.drop_table("mt5_prop_daily_state")

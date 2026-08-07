"""add mt5_broker_recovered_trades table for post-incident broker-history recovery

Revision ID: 0037_mt5_broker_recovered_trades
Revises: 0036_db_incident_boundary
Create Date: 2026-08-07

Additive only. One-time recovery target for backend/scripts/recover_mt5_broker_history.py
(see Part 7-9 of the 2026-08-07 disaster-recovery directive, and the db_incidents
table from migration 0036). Deliberately a SEPARATE table from mt5_trade_records:
that table's schema assumes rich internal Bensim context (cycle_id, candidate_id,
ai_decision_id, strategy_outputs, consensus, market_regime, ...) that simply does
not exist for broker-recovered history -- forcing recovered rows into it would mean
either violating its NOT NULL columns or fabricating internal context that was
never real (explicitly prohibited by Part 8 of the recovery directive). Every
column here is broker-observable fact only; anything not retrievable from the
broker is left NULL, never guessed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_mt5_broker_recovered_trades"
down_revision: Union[str, Sequence[str], None] = "0036_db_incident_boundary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_broker_recovered_trades",
        sa.Column("recovery_record_id", sa.String(length=64), nullable=False),
        sa.Column("account_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("deal_ticket", sa.BigInteger(), nullable=True),
        sa.Column("order_ticket", sa.BigInteger(), nullable=True),
        sa.Column("position_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("deal_type", sa.String(length=32), nullable=True),
        sa.Column("entry_type", sa.String(length=32), nullable=True),
        sa.Column("volume", sa.Numeric(18, 4), nullable=True),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column("deal_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_setup_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_done_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profit", sa.Numeric(18, 4), nullable=True),
        sa.Column("commission", sa.Numeric(18, 4), nullable=True),
        sa.Column("swap", sa.Numeric(18, 4), nullable=True),
        sa.Column("fee", sa.Numeric(18, 4), nullable=True),
        sa.Column("comment", sa.String(length=256), nullable=True),
        sa.Column("magic", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 6), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 6), nullable=True),
        sa.Column("broker_retcode", sa.String(length=64), nullable=True),
        sa.Column("broker_status", sa.String(length=64), nullable=True),
        sa.Column("raw_deal_payload", sa.JSON(), nullable=True),
        sa.Column("raw_order_payload", sa.JSON(), nullable=True),
        sa.Column("data_origin", sa.String(length=32), nullable=False, server_default="MT5_BROKER_RECOVERY"),
        sa.Column("recovered_after_incident", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recovery_incident_id", sa.String(length=64), nullable=False, server_default="DB_DROP_20260807"),
        sa.Column("recovered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("recovery_record_id"),
        sa.UniqueConstraint("account_fingerprint", "deal_ticket", name="uq_mt5_recovered_deal"),
    )
    op.create_index("ix_mt5_broker_recovered_trades_symbol", "mt5_broker_recovered_trades", ["symbol"])
    op.create_index("ix_mt5_broker_recovered_trades_position_id", "mt5_broker_recovered_trades", ["position_id"])
    op.create_index("ix_mt5_broker_recovered_trades_deal_time", "mt5_broker_recovered_trades", ["deal_time"])


def downgrade() -> None:
    op.drop_table("mt5_broker_recovered_trades")

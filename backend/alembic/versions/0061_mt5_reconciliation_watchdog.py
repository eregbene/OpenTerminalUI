"""MT5 broker/internal state reconciliation watchdog (Phase 3)

Revision ID: 0061_mt5_reconciliation_watchdog
Revises: 0060_walk_forward_results
Create Date: 2026-08-13

Adds mt5_account_reconciliation_status (latest per-account verdict, upserted) and
mt5_account_reconciliation_findings (append-only discrepancy audit trail). Purely additive,
idempotent, does not touch mt5_positions/adaptive_position_states or any existing sync logic.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061_mt5_reconciliation_watchdog"
down_revision: Union[str, Sequence[str], None] = "0060_walk_forward_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_table_if_missing(inspector: sa.Inspector, name: str, *columns: sa.Column, **kwargs) -> None:
    if name in inspector.get_table_names():
        return
    op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str], **kwargs) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _create_table_if_missing(
        inspector, "mt5_account_reconciliation_status",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("trustworthy", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("finding_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing(inspector, "ix_mars_trustworthy", "mt5_account_reconciliation_status", ["trustworthy"])
    _create_index_if_missing(inspector, "ix_mars_last_checked_at", "mt5_account_reconciliation_status", ["last_checked_at"])

    _create_table_if_missing(
        inspector, "mt5_account_reconciliation_findings",
        sa.Column("finding_id", sa.String(160), primary_key=True),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("finding_type", sa.String(32), nullable=False),
        sa.Column("position_id", sa.String(96), nullable=True),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("account_id", "finding_type", "position_id", "created_at"):
        _create_index_if_missing(inspector, f"ix_mrf_{col}", "mt5_account_reconciliation_findings", [col])
    _create_index_if_missing(inspector, "ix_mrf_account_time", "mt5_account_reconciliation_findings", ["account_id", "created_at"])


def downgrade() -> None:
    op.drop_table("mt5_account_reconciliation_findings")
    op.drop_table("mt5_account_reconciliation_status")

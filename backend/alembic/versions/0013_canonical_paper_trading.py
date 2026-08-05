"""canonical paper trading controls

Revision ID: 0013_canonical_paper_trading
Revises: 0012_strategy_research
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_canonical_paper_trading"
down_revision = "0012_strategy_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in [
        "paper_accounts",
        "strategy_deployments",
        "risk_policies",
        "risk_evaluations",
        "paper_orders",
        "paper_fills",
        "portfolio_ledger_entries",
        "portfolio_snapshots",
        "emergency_controls",
        "trading_reconciliations",
        "trading_audit_records",
    ]:
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("account_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("deployment_id", sa.String(length=64), nullable=True, index=True),
            sa.Column("status", sa.String(length=64), nullable=True, index=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table_name in reversed(
        [
            "paper_accounts",
            "strategy_deployments",
            "risk_policies",
            "risk_evaluations",
            "paper_orders",
            "paper_fills",
            "portfolio_ledger_entries",
            "portfolio_snapshots",
            "emergency_controls",
            "trading_reconciliations",
            "trading_audit_records",
        ]
    ):
        op.drop_table(table_name)

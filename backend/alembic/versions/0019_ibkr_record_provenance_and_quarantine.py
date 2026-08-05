"""ibkr record provenance and quarantine

Revision ID: 0019_ibkr_record_provenance_and_quarantine
Revises: 0018_ibkr_paper_acceptance_persistence
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_ibkr_record_provenance_and_quarantine"
down_revision = "0018_ibkr_paper_acceptance_persistence"
branch_labels = None
depends_on = None


def _add_lineage_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("record_source", sa.String(32), nullable=False, server_default="UNKNOWN_LEGACY"))
    op.add_column(table_name, sa.Column("is_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(table_name, sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table_name, sa.Column("quarantine_reason", sa.String(160), nullable=True))
    op.add_column(table_name, sa.Column("test_run_id", sa.String(120), nullable=True))
    op.create_index(f"ix_{table_name}_record_source", table_name, ["record_source"])
    op.create_index(f"ix_{table_name}_is_quarantined", table_name, ["is_quarantined"])
    op.create_index(f"ix_{table_name}_test_run_id", table_name, ["test_run_id"])


def upgrade() -> None:
    _add_lineage_columns("broker_contracts")
    _add_lineage_columns("broker_orders")
    _add_lineage_columns("broker_events")
    _add_lineage_columns("broker_executions")
    op.add_column("broker_orders", sa.Column("broker_session_id", sa.String(120), nullable=True))
    op.create_index("ix_broker_orders_broker_session_id", "broker_orders", ["broker_session_id"])
    op.create_index(
        "ix_broker_order_reconciliation_scope",
        "broker_orders",
        ["broker", "environment", "masked_account_id", "record_source", "is_quarantined"],
    )
    op.add_column("broker_reconciliations", sa.Column("scope_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))

    op.execute(
        """
        UPDATE broker_contracts
        SET record_source = CASE
            WHEN id ILIKE 'test_%' OR id ILIKE '%fx5b%' OR contract_payload::text ILIKE '%test_%' THEN 'FIXTURE_IBKR'
            WHEN verification_source IN ('REAL_IBKR', 'REAL_IBKR_PAPER') AND con_id NOT IN (0, 123) THEN 'REAL_IBKR'
            WHEN verification_source = 'FIXTURE' THEN 'FIXTURE_IBKR'
            ELSE 'UNKNOWN_LEGACY'
        END
        """
    )
    op.execute(
        """
        UPDATE broker_orders
        SET record_source = CASE
            WHEN id ILIKE 'test_%' OR id ILIKE '%fx5b%' OR order_reference ILIKE 'ref_fx5b%' OR client_order_id ILIKE 'client_fx5b%' THEN 'FIXTURE_IBKR'
            WHEN broker_order_id IS NOT NULL AND broker_order_id !~ '^(9001|test|fixture)' THEN 'REAL_IBKR'
            ELSE 'UNKNOWN_LEGACY'
        END,
        test_run_id = CASE
            WHEN id ILIKE 'test_%' OR id ILIKE '%fx5b%' OR order_reference ILIKE 'ref_fx5b%' THEN 'fx5b'
            ELSE test_run_id
        END
        """
    )
    op.execute(
        """
        UPDATE broker_events
        SET record_source = COALESCE((SELECT record_source FROM broker_orders WHERE broker_orders.id = broker_events.broker_order_id), 'UNKNOWN_LEGACY'),
            test_run_id = COALESCE((SELECT test_run_id FROM broker_orders WHERE broker_orders.id = broker_events.broker_order_id), test_run_id)
        """
    )
    op.execute(
        """
        UPDATE broker_executions
        SET record_source = COALESCE((SELECT record_source FROM broker_orders WHERE broker_orders.id = broker_executions.broker_order_id), 'UNKNOWN_LEGACY'),
            test_run_id = COALESCE((SELECT test_run_id FROM broker_orders WHERE broker_orders.id = broker_executions.broker_order_id), test_run_id)
        """
    )


def downgrade() -> None:
    op.drop_column("broker_reconciliations", "scope_metadata")
    op.drop_index("ix_broker_order_reconciliation_scope", table_name="broker_orders")
    op.drop_index("ix_broker_orders_broker_session_id", table_name="broker_orders")
    op.drop_column("broker_orders", "broker_session_id")
    for table_name in ("broker_executions", "broker_events", "broker_orders", "broker_contracts"):
        op.drop_index(f"ix_{table_name}_test_run_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_is_quarantined", table_name=table_name)
        op.drop_index(f"ix_{table_name}_record_source", table_name=table_name)
        op.drop_column(table_name, "test_run_id")
        op.drop_column(table_name, "quarantine_reason")
        op.drop_column(table_name, "quarantined_at")
        op.drop_column(table_name, "is_quarantined")
        op.drop_column(table_name, "record_source")

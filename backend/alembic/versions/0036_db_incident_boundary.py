"""add db_incidents table and record the 2026-08-07 drop_all incident boundary

Revision ID: 0036_db_incident_boundary
Revises: 0035_close_missing_orm_tables
Create Date: 2026-08-07

Additive only. A minimal forensic/audit table giving future analytics an explicit
boundary marker for the 2026-08-07 data-loss incident (Base.metadata.drop_all()
executed against the shared dev DATABASE_URL by test fixtures in test_alerts_v2.py /
test_notifications.py / test_wave5_public_api.py, and potentially others -- see
Part 15 audit). Any application-internal history with created_at/timestamps before
this boundary and not explicitly reconstructed (see recovered_after_incident /
data_origin markers added by the MT5 broker-history recovery service) should be
treated as lost, not merely absent.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_db_incident_boundary"
down_revision: Union[str, Sequence[str], None] = "0035_close_missing_orm_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "db_incidents",
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("recovery", sa.Text(), nullable=False),
        sa.Column("data_loss", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("incident_id"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO db_incidents
                (incident_id, occurred_at_start, occurred_at_end, detected_at, cause, recovery, data_loss, notes)
            VALUES
                (
                    'DB_DROP_20260807',
                    '2026-08-07T10:49:02Z',
                    '2026-08-07T11:30:51Z',
                    '2026-08-07T12:23:07Z',
                    'pytest destructive fixtures (Base.metadata.drop_all(bind=engine)) in test_alerts_v2.py, '
                    'test_notifications.py, test_wave5_public_api.py used backend.shared.db.engine -- the same '
                    'engine as the live/shared development DATABASE_URL -- instead of an isolated test database. '
                    'Exact DROP TABLE statement timing could not be captured (log_statement=none); window bounded '
                    'from Postgres error-log evidence.',
                    'Damaged volume (openterminalui_openterminalui_postgres_data) preserved untouched and never '
                    'repaired in place. New database built entirely via alembic upgrade head (0001->0036) on a '
                    'fresh volume (openterminalui_postgres_recovered_20260807). Surviving pre-incident data '
                    '(pcr_snapshots, 198 rows) restored from pg_dump export. MT5 broker-history reconstruction '
                    'used to recover as much trade history as the broker retains, marked data_origin=MT5_BROKER_RECOVERY.',
                    'All application-internal history in ORM/Alembic-managed tables prior to this incident is lost: '
                    'adaptive management state/actions, portfolio execution snapshots/orders, MT5 trade memory, '
                    'decision context, economic intelligence persistence, alerts/notifications, research artifacts, '
                    'and all other tables governed by backend.shared.db.Base. Broker-side execution history (deals/'
                    'orders/positions) is reconstructable from MT5 where the broker still retains it; internal '
                    'reasoning/context/policy state around those trades is not reconstructable and is recorded as '
                    'NULL/NOT_RECOVERABLE rather than fabricated.',
                    'iv_snapshots and pcr_snapshots are raw-SQL self-provisioning tables outside Base.metadata and '
                    'were never touched by the drop_all() call; pcr_snapshots data therefore predates and survives '
                    'this incident boundary untouched.'
                )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("db_incidents")

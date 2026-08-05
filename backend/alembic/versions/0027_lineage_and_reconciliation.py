"""trade lineage capture and closed-position reconciliation tracking

Revision ID: 0027_lineage_and_reconciliation
Revises: 0026_tp_progress_protection
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_lineage_and_reconciliation"
down_revision = "0026_tp_progress_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("adaptive_position_states", sa.Column("strategy_id", sa.String(32), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("timeframe", sa.String(16), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("closed_detected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_adaptive_position_states_strategy_id", "adaptive_position_states", ["strategy_id"])
    op.create_index("ix_adaptive_position_states_timeframe", "adaptive_position_states", ["timeframe"])
    op.create_index("ix_adaptive_position_states_closed_detected_at", "adaptive_position_states", ["closed_detected_at"])


def downgrade() -> None:
    op.drop_index("ix_adaptive_position_states_closed_detected_at", table_name="adaptive_position_states")
    op.drop_index("ix_adaptive_position_states_timeframe", table_name="adaptive_position_states")
    op.drop_index("ix_adaptive_position_states_strategy_id", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "closed_detected_at")
    op.drop_column("adaptive_position_states", "timeframe")
    op.drop_column("adaptive_position_states", "strategy_id")

"""trade intelligence: track auto-replay completion per closed position

Revision ID: 0031_trade_intelligence_replay_tracking
Revises: 0030_adaptive_trade_manager_v2
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_trade_intelligence_replay_tracking"
down_revision = "0030_adaptive_trade_manager_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("adaptive_position_states", sa.Column("replay_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_adaptive_position_states_replay_completed_at", "adaptive_position_states", ["replay_completed_at"])


def downgrade() -> None:
    op.drop_index("ix_adaptive_position_states_replay_completed_at", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "replay_completed_at")

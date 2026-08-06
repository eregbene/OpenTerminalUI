"""trade intelligence: persist market regime/ATR/volatility/spread at entry time

Revision ID: 0032_trade_intelligence_entry_context
Revises: 0031_trade_intelligence_replay_tracking
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_trade_intelligence_entry_context"
down_revision = "0031_trade_intelligence_replay_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("adaptive_position_states", sa.Column("entry_regime", sa.String(32), nullable=True))
    op.create_index("ix_adaptive_position_states_entry_regime", "adaptive_position_states", ["entry_regime"])
    op.add_column("adaptive_position_states", sa.Column("entry_regime_confidence", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("entry_atr", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("entry_volatility", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("entry_spread", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("adaptive_position_states", "entry_spread")
    op.drop_column("adaptive_position_states", "entry_volatility")
    op.drop_column("adaptive_position_states", "entry_atr")
    op.drop_column("adaptive_position_states", "entry_regime_confidence")
    op.drop_index("ix_adaptive_position_states_entry_regime", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "entry_regime")

"""add deterministic confidence columns to mt5_scheduler_candidates

Revision ID: 0038_mt5_candidate_confidence_columns
Revises: 0037_mt5_broker_recovered_trades
Create Date: 2026-08-10

Additive only. Part of removing OpenAI from the MT5 autonomous entry-decision path
(backend/brokers/mt5/autonomous.py, backend/brokers/mt5/confidence.py) and replacing
it with a deterministic trade_confidence_score (0-100). Adds three nullable columns
to the existing mt5_scheduler_candidates table so rank/selection/confidence are
first-class queryable facts rather than buried in the existing raw_payload JSON blob:

- trade_confidence_score: the deterministic 0-100 score (NULL for historical rows
  persisted before this change -- those were AI-decided, not confidence-scored, and
  backfilling a score for them would be fabricated data)
- rank: this candidate's deterministic rank within its cycle's scored pool (1 = best)
- selected: whether this candidate was the one actually attempted for execution
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_mt5_candidate_confidence_columns"
down_revision: Union[str, Sequence[str], None] = "0037_mt5_broker_recovered_trades"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mt5_scheduler_candidates", sa.Column("trade_confidence_score", sa.Float(), nullable=True))
    op.add_column("mt5_scheduler_candidates", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("mt5_scheduler_candidates", sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_mt5_scheduler_candidates_trade_confidence_score", "mt5_scheduler_candidates", ["trade_confidence_score"])
    op.create_index("ix_mt5_scheduler_candidates_selected", "mt5_scheduler_candidates", ["selected"])


def downgrade() -> None:
    op.drop_index("ix_mt5_scheduler_candidates_selected", table_name="mt5_scheduler_candidates")
    op.drop_index("ix_mt5_scheduler_candidates_trade_confidence_score", table_name="mt5_scheduler_candidates")
    op.drop_column("mt5_scheduler_candidates", "selected")
    op.drop_column("mt5_scheduler_candidates", "rank")
    op.drop_column("mt5_scheduler_candidates", "trade_confidence_score")

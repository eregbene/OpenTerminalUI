"""add MTFAI1 confirmation-gate columns to mt5_candidate_evaluations

Revision ID: 0042_mtfai1_confirmation_gate
Revises: 0041_mt5_multi_strategy_calibration
Create Date: 2026-08-12

Additive only. Part of the DEMO-only MTFAI1 entry-quality experiment: a standalone MTFAI1
candidate (no independent confirming signal on the same symbol+direction this cycle) is
deferred in favor of the next eligible non-MTFAI1 candidate. These two nullable columns record,
for the one executed candidate each cycle when it is MTFAI1, whether it was confirmed and by
which strategies -- so later analytics can compare standalone-vs-confirmed-vs-non-MTFAI1 trade
outcomes without needing to re-derive the verdict retroactively. Historical rows (written
before this experiment existed) simply have both columns NULL/empty, which the comparison
report treats as its own "unclassified" bucket rather than guessing.

Does not touch any existing table, weight, threshold, or trading-decision path.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_mtfai1_confirmation_gate"
down_revision: Union[str, Sequence[str], None] = "0041_mt5_multi_strategy_calibration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mt5_candidate_evaluations", sa.Column("mtfai1_confirmed", sa.Boolean(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("mtfai1_confirming_strategy_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.create_index("ix_mt5_cand_eval_mtfai1_confirmed", "mt5_candidate_evaluations", ["mtfai1_confirmed"])


def downgrade() -> None:
    op.drop_index("ix_mt5_cand_eval_mtfai1_confirmed", table_name="mt5_candidate_evaluations")
    op.drop_column("mt5_candidate_evaluations", "mtfai1_confirming_strategy_ids")
    op.drop_column("mt5_candidate_evaluations", "mtfai1_confirmed")

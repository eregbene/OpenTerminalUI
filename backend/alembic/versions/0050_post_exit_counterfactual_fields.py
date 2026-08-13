"""extend adaptive_manager_counterfactuals with richer post-exit shadow-tracking fields

Revision ID: 0050_post_exit_counterfactual_fields
Revises: 0049_adaptive_idempotency_account_scope
Create Date: 2026-08-12

Part 8 (post-exit counterfactual resolver) audit/fix. The resolver
(backend/adaptive_management/outcome_resolver.py) had never resolved a single
post_exit_status row -- root causes fixed alongside this migration:
  1. It used a single hardcoded MT5 adapter (the demo_10k default) for every account, so
     ftmo_demo_25k/50k/100k candle fetches silently targeted the wrong broker terminal.
  2. Closed positions whose AdaptivePositionBaselineORM row was never written (opened before
     the baseline-capture hook went live) could never get a counterfactual row created at all,
     permanently and silently excluding them.
  3. The 4 existing boolean flags (reached_original_tp/reached_plus_1r/reversed_strongly/
     would_have_hit_original_sl) were too coarse to distinguish the four outcome shapes the
     spec asks for (immediate reversal / mild continuation / later TP / substantial R left on
     the table) -- this migration adds explicit fields for that instead of overloading the
     existing ones.

Does not touch any trading-decision path, threshold, or existing column's meaning.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_post_exit_counterfactual_fields"
down_revision: Union[str, Sequence[str], None] = "0049_adaptive_idempotency_account_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_mfe_r", sa.Float(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_mae_r", sa.Float(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_additional_r_available", sa.Float(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_time_to_continuation_seconds", sa.Integer(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_time_to_reversal_seconds", sa.Integer(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_classification", sa.String(length=32), nullable=False, server_default="PENDING"))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_candles_scanned", sa.Integer(), nullable=True))
    op.add_column("adaptive_manager_counterfactuals", sa.Column("post_exit_data_note", sa.String(length=64), nullable=True))
    op.alter_column("adaptive_manager_counterfactuals", "post_exit_classification", server_default=None)
    op.create_index("ix_adaptive_manager_counterfactuals_post_exit_classification", "adaptive_manager_counterfactuals", ["post_exit_classification"])


def downgrade() -> None:
    op.drop_index("ix_adaptive_manager_counterfactuals_post_exit_classification", table_name="adaptive_manager_counterfactuals")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_data_note")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_candles_scanned")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_classification")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_time_to_reversal_seconds")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_time_to_continuation_seconds")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_additional_r_available")
    op.drop_column("adaptive_manager_counterfactuals", "post_exit_mae_r")

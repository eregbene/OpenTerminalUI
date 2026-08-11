"""add multi-strategy calibration columns to mt5_candidate_evaluations

Revision ID: 0041_mt5_multi_strategy_calibration
Revises: 0040_adaptive_manager_validation
Create Date: 2026-08-10

Additive only. Part of connecting the existing strategy families already implemented in the
codebase (backend/mt5_strategies/) to the MT5 autonomous trading pipeline. Extends the
existing mt5_candidate_evaluations table (Confidence Validation & Calibration layer) with
strategy-family/fusion/conflict/SMC-evidence columns so the calibration layer can eventually
compare expectancy across strategies, regimes, and evidence types -- per the unified
multi-strategy engine's calibration instrumentation requirement.

Does not touch any existing table, weight, threshold, or trading-decision path. All new
columns are nullable or default-empty, so every existing row remains valid unchanged.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_mt5_multi_strategy_calibration"
down_revision: Union[str, Sequence[str], None] = "0040_adaptive_manager_validation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mt5_candidate_evaluations", sa.Column("strategy_family", sa.String(64), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("contributing_strategies", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("mt5_candidate_evaluations", sa.Column("contributing_families", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("mt5_candidate_evaluations", sa.Column("multi_strategy_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("mt5_candidate_evaluations", sa.Column("conflict_state", sa.String(32), nullable=False, server_default="NONE"))
    op.add_column("mt5_candidate_evaluations", sa.Column("htf_direction_h4", sa.String(16), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("htf_direction_h1", sa.String(16), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("raw_signal_strength", sa.Float(), nullable=True))
    op.add_column("mt5_candidate_evaluations", sa.Column("smc_evidence", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("mt5_candidate_evaluations", sa.Column("strategy_evidence", sa.JSON(), nullable=False, server_default="{}"))

    op.create_index("ix_mt5_cand_eval_strategy_family", "mt5_candidate_evaluations", ["strategy_family"])
    op.create_index("ix_mt5_cand_eval_multi_strategy_confirmation", "mt5_candidate_evaluations", ["multi_strategy_confirmation"])
    op.create_index("ix_mt5_cand_eval_conflict_state", "mt5_candidate_evaluations", ["conflict_state"])
    op.create_index("ix_mt5_cand_eval_htf_direction_h4", "mt5_candidate_evaluations", ["htf_direction_h4"])
    op.create_index("ix_mt5_cand_eval_htf_direction_h1", "mt5_candidate_evaluations", ["htf_direction_h1"])


def downgrade() -> None:
    op.drop_index("ix_mt5_cand_eval_htf_direction_h1", table_name="mt5_candidate_evaluations")
    op.drop_index("ix_mt5_cand_eval_htf_direction_h4", table_name="mt5_candidate_evaluations")
    op.drop_index("ix_mt5_cand_eval_conflict_state", table_name="mt5_candidate_evaluations")
    op.drop_index("ix_mt5_cand_eval_multi_strategy_confirmation", table_name="mt5_candidate_evaluations")
    op.drop_index("ix_mt5_cand_eval_strategy_family", table_name="mt5_candidate_evaluations")
    op.drop_column("mt5_candidate_evaluations", "strategy_evidence")
    op.drop_column("mt5_candidate_evaluations", "smc_evidence")
    op.drop_column("mt5_candidate_evaluations", "raw_signal_strength")
    op.drop_column("mt5_candidate_evaluations", "htf_direction_h1")
    op.drop_column("mt5_candidate_evaluations", "htf_direction_h4")
    op.drop_column("mt5_candidate_evaluations", "conflict_state")
    op.drop_column("mt5_candidate_evaluations", "multi_strategy_confirmation")
    op.drop_column("mt5_candidate_evaluations", "contributing_families")
    op.drop_column("mt5_candidate_evaluations", "contributing_strategies")
    op.drop_column("mt5_candidate_evaluations", "strategy_family")

"""add mt5_candidate_evaluations table (Confidence Validation & Calibration layer)

Revision ID: 0039_mt5_candidate_evaluations
Revises: 0038_mt5_candidate_confidence_columns
Create Date: 2026-08-10

Additive only. New, dedicated, append-only table for the Confidence Validation &
Calibration layer (backend/brokers/mt5/candidate_evaluation.py,
backend/brokers/mt5/outcome_resolver.py, backend/brokers/mt5/confidence_calibration.py).

Deliberately separate from mt5_scheduler_candidates (the trading engine's own operational
bookkeeping table, which is upserted per cycle via db.merge()). This table exists purely to
answer "did confidence predict trade quality" -- one immutable row per fully confidence-
scored candidate (selected, lower-ranked, or rejected), decision-time fields written once at
insert and never overwritten, outcome fields filled in strictly afterward by either the
shadow tracker (non-executed candidates) or the executed-trade linker.

Does not touch any existing table, weight, threshold, or trading-decision path.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_mt5_candidate_evaluations"
down_revision: Union[str, Sequence[str], None] = "0038_mt5_candidate_confidence_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_candidate_evaluations",
        sa.Column("evaluation_id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("candidate_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=True),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("market_regime", sa.String(64), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(32), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("raw_trend_score", sa.Float(), nullable=True),
        sa.Column("rule_version", sa.String(32), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("eligible_for_execution", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("proposed_entry", sa.Float(), nullable=True),
        sa.Column("proposed_stop_loss", sa.Float(), nullable=True),
        sa.Column("proposed_take_profit", sa.Float(), nullable=True),
        sa.Column("initial_reward_risk", sa.Float(), nullable=True),
        sa.Column("spread", sa.Float(), nullable=True),
        sa.Column("atr", sa.Float(), nullable=True),
        sa.Column("portfolio_state_snapshot", sa.JSON(), nullable=False),
        sa.Column("open_positions_snapshot", sa.JSON(), nullable=False),
        sa.Column("correlated_exposure_snapshot", sa.JSON(), nullable=False),
        sa.Column("outcome_type", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("broker_ticket", sa.String(64), nullable=True),
        sa.Column("outcome_status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("outcome_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tp_hit", sa.Boolean(), nullable=True),
        sa.Column("sl_hit", sa.Boolean(), nullable=True),
        sa.Column("mfe_r", sa.Float(), nullable=True),
        sa.Column("mae_r", sa.Float(), nullable=True),
        sa.Column("hypothetical_r", sa.Float(), nullable=True),
        sa.Column("realized_r", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("time_to_tp_seconds", sa.Float(), nullable=True),
        sa.Column("time_to_sl_seconds", sa.Float(), nullable=True),
        sa.Column("time_to_mfe_seconds", sa.Float(), nullable=True),
        sa.Column("holding_duration_seconds", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(64), nullable=True),
        sa.Column("actual_entry", sa.Float(), nullable=True),
        sa.Column("slippage", sa.Float(), nullable=True),
        sa.Column("final_exit_price", sa.Float(), nullable=True),
        sa.Column("expiry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_payload", sa.JSON(), nullable=False),
        sa.Column("outcome_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("candidate_id", name="uq_mt5_cand_eval_candidate_id"),
    )
    op.create_index("ix_mt5_cand_eval_cycle_id", "mt5_candidate_evaluations", ["cycle_id"])
    op.create_index("ix_mt5_cand_eval_candidate_id", "mt5_candidate_evaluations", ["candidate_id"])
    op.create_index("ix_mt5_cand_eval_created_at", "mt5_candidate_evaluations", ["created_at"])
    op.create_index("ix_mt5_cand_eval_symbol", "mt5_candidate_evaluations", ["symbol"])
    op.create_index("ix_mt5_cand_eval_broker_symbol", "mt5_candidate_evaluations", ["broker_symbol"])
    op.create_index("ix_mt5_cand_eval_direction", "mt5_candidate_evaluations", ["direction"])
    op.create_index("ix_mt5_cand_eval_timeframe", "mt5_candidate_evaluations", ["timeframe"])
    op.create_index("ix_mt5_cand_eval_strategy", "mt5_candidate_evaluations", ["strategy"])
    op.create_index("ix_mt5_cand_eval_market_regime", "mt5_candidate_evaluations", ["market_regime"])
    op.create_index("ix_mt5_cand_eval_session", "mt5_candidate_evaluations", ["session"])
    op.create_index("ix_mt5_cand_eval_overall_confidence", "mt5_candidate_evaluations", ["overall_confidence"])
    op.create_index("ix_mt5_cand_eval_confidence_band", "mt5_candidate_evaluations", ["confidence_band"])
    op.create_index("ix_mt5_cand_eval_rank", "mt5_candidate_evaluations", ["rank"])
    op.create_index("ix_mt5_cand_eval_selected", "mt5_candidate_evaluations", ["selected"])
    op.create_index("ix_mt5_cand_eval_eligible", "mt5_candidate_evaluations", ["eligible_for_execution"])
    op.create_index("ix_mt5_cand_eval_outcome_type", "mt5_candidate_evaluations", ["outcome_type"])
    op.create_index("ix_mt5_cand_eval_broker_ticket", "mt5_candidate_evaluations", ["broker_ticket"])
    op.create_index("ix_mt5_cand_eval_outcome_status", "mt5_candidate_evaluations", ["outcome_status"])
    op.create_index("ix_mt5_cand_eval_outcome_resolved_at", "mt5_candidate_evaluations", ["outcome_resolved_at"])
    op.create_index("ix_mt5_cand_eval_exit_reason", "mt5_candidate_evaluations", ["exit_reason"])
    op.create_index("ix_mt5_cand_eval_expiry_at", "mt5_candidate_evaluations", ["expiry_at"])
    op.create_index("ix_mt5_cand_eval_band_outcome", "mt5_candidate_evaluations", ["confidence_band", "outcome_type", "outcome_status"])
    op.create_index("ix_mt5_cand_eval_cycle_rank", "mt5_candidate_evaluations", ["cycle_id", "rank"])


def downgrade() -> None:
    op.drop_table("mt5_candidate_evaluations")

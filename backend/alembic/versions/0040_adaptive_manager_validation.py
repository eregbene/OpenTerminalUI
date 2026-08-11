"""add adaptive manager validation & performance analytics tables

Revision ID: 0040_adaptive_manager_validation
Revises: 0039_mt5_candidate_evaluations
Create Date: 2026-08-10

Additive only. Three new, dedicated tables for the Adaptive Trade Manager Validation &
Performance Analytics layer (backend/adaptive_management/event_capture.py,
backend/adaptive_management/outcome_resolver.py, backend/adaptive_management/analytics.py):

- adaptive_management_events: append-only per-position, per-cycle decision journal, captured
  immediately after AdaptiveManagementService._persist_action -- never influences execution.
- adaptive_position_baselines: immutable original-trade-state snapshot, written once.
- adaptive_manager_counterfactuals: analytics-only counterfactual cache (original SL/TP
  baseline, no-BE baseline, post-exit shadow tracking), resolved strictly after the fact.

Does not touch any existing table, weight, threshold, break-even rule, trailing rule, or
management-decision path.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_adaptive_manager_validation"
down_revision: Union[str, Sequence[str], None] = "0039_mt5_candidate_evaluations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adaptive_management_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column("cycle_run_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("original_sl", sa.Float(), nullable=True),
        sa.Column("original_tp", sa.Float(), nullable=True),
        sa.Column("sl_before", sa.Float(), nullable=True),
        sa.Column("sl_after", sa.Float(), nullable=True),
        sa.Column("tp_before", sa.Float(), nullable=True),
        sa.Column("tp_after", sa.Float(), nullable=True),
        sa.Column("current_r", sa.Float(), nullable=True),
        sa.Column("max_achieved_r", sa.Float(), nullable=True),
        sa.Column("min_achieved_r", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_category", sa.String(32), nullable=False),
        sa.Column("action_status", sa.String(32), nullable=False),
        sa.Column("action_reason", sa.Text(), nullable=True),
        sa.Column("manager_state", sa.JSON(), nullable=False),
        sa.Column("is_at_or_beyond_breakeven", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_trailing_action", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strategy", sa.String(64), nullable=True),
        sa.Column("market_regime", sa.String(64), nullable=True),
        sa.Column("atr", sa.Float(), nullable=True),
        sa.Column("structure_level", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_adaptive_mgmt_events_cycle_run_id", "adaptive_management_events", ["cycle_run_id"])
    op.create_index("ix_adaptive_mgmt_events_created_at", "adaptive_management_events", ["created_at"])
    op.create_index("ix_adaptive_mgmt_events_position_id", "adaptive_management_events", ["position_id"])
    op.create_index("ix_adaptive_mgmt_events_symbol", "adaptive_management_events", ["symbol"])
    op.create_index("ix_adaptive_mgmt_events_direction", "adaptive_management_events", ["direction"])
    op.create_index("ix_adaptive_mgmt_events_action_type", "adaptive_management_events", ["action_type"])
    op.create_index("ix_adaptive_mgmt_events_action_category", "adaptive_management_events", ["action_category"])
    op.create_index("ix_adaptive_mgmt_events_action_status", "adaptive_management_events", ["action_status"])
    op.create_index("ix_adaptive_mgmt_events_is_be", "adaptive_management_events", ["is_at_or_beyond_breakeven"])
    op.create_index("ix_adaptive_mgmt_events_is_trailing", "adaptive_management_events", ["is_trailing_action"])
    op.create_index("ix_adaptive_mgmt_events_strategy", "adaptive_management_events", ["strategy"])
    op.create_index("ix_adaptive_mgmt_events_regime", "adaptive_management_events", ["market_regime"])
    op.create_index("ix_adaptive_mgmt_events_position_time", "adaptive_management_events", ["position_id", "created_at"])

    op.create_table(
        "adaptive_position_baselines",
        sa.Column("position_id", sa.String(96), primary_key=True),
        sa.Column("broker_ticket", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("original_entry", sa.Float(), nullable=True),
        sa.Column("original_sl", sa.Float(), nullable=True),
        sa.Column("original_tp", sa.Float(), nullable=True),
        sa.Column("initial_stop_distance", sa.Float(), nullable=True),
        sa.Column("initial_reward_risk", sa.Float(), nullable=True),
        sa.Column("initial_risk_money", sa.Float(), nullable=True),
        sa.Column("original_strategy", sa.String(64), nullable=True),
        sa.Column("original_confidence", sa.Float(), nullable=True),
        sa.Column("confidence_band", sa.String(32), nullable=True),
        sa.Column("candidate_rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_adaptive_baseline_broker_ticket", "adaptive_position_baselines", ["broker_ticket"])
    op.create_index("ix_adaptive_baseline_symbol", "adaptive_position_baselines", ["symbol"])
    op.create_index("ix_adaptive_baseline_direction", "adaptive_position_baselines", ["direction"])
    op.create_index("ix_adaptive_baseline_strategy", "adaptive_position_baselines", ["original_strategy"])
    op.create_index("ix_adaptive_baseline_band", "adaptive_position_baselines", ["confidence_band"])
    op.create_index("ix_adaptive_baseline_created_at", "adaptive_position_baselines", ["created_at"])

    op.create_table(
        "adaptive_manager_counterfactuals",
        sa.Column("position_id", sa.String(96), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("original_sltp_outcome", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("original_sltp_r", sa.Float(), nullable=True),
        sa.Column("original_sltp_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("no_be_applicable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("no_be_outcome", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("no_be_r", sa.Float(), nullable=True),
        sa.Column("no_be_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_exit_status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("post_exit_reached_original_tp", sa.Boolean(), nullable=True),
        sa.Column("post_exit_reached_plus_1r", sa.Boolean(), nullable=True),
        sa.Column("post_exit_reversed_strongly", sa.Boolean(), nullable=True),
        sa.Column("post_exit_would_have_hit_original_sl", sa.Boolean(), nullable=True),
        sa.Column("post_exit_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_adaptive_cf_symbol", "adaptive_manager_counterfactuals", ["symbol"])
    op.create_index("ix_adaptive_cf_original_sltp_outcome", "adaptive_manager_counterfactuals", ["original_sltp_outcome"])
    op.create_index("ix_adaptive_cf_no_be_applicable", "adaptive_manager_counterfactuals", ["no_be_applicable"])
    op.create_index("ix_adaptive_cf_no_be_outcome", "adaptive_manager_counterfactuals", ["no_be_outcome"])
    op.create_index("ix_adaptive_cf_post_exit_status", "adaptive_manager_counterfactuals", ["post_exit_status"])
    op.create_index("ix_adaptive_cf_expiry_at", "adaptive_manager_counterfactuals", ["expiry_at"])


def downgrade() -> None:
    op.drop_table("adaptive_manager_counterfactuals")
    op.drop_table("adaptive_position_baselines")
    op.drop_table("adaptive_management_events")

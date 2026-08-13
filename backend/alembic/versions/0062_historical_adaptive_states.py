"""Historical Adaptive Intelligence backfill: state reconstruction + outcome resolution

Revision ID: 0062_historical_adaptive_states
Revises: 0061_mt5_reconciliation_watchdog
Create Date: 2026-08-13

Adds historical_adaptive_states (reconstructed intermediate trade-state checkpoints from the
historical replay corpus) and historical_adaptive_outcomes (what happened after each state).
Purely additive, idempotent -- does not touch adaptive_management_events/
adaptive_position_baselines/adaptive_manager_counterfactuals (the REAL per-position tables) or
any live-trading table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062_historical_adaptive_states"
down_revision: Union[str, Sequence[str], None] = "0061_mt5_reconciliation_watchdog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_table_if_missing(inspector: sa.Inspector, name: str, *columns: sa.Column, **kwargs) -> None:
    if name in inspector.get_table_names():
        return
    op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str], **kwargs) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _create_table_if_missing(
        inspector, "historical_adaptive_states",
        sa.Column("state_id", sa.String(160), primary_key=True),
        sa.Column("source_fingerprint_id", sa.String(160), nullable=False),
        sa.Column("historical_intelligence_version", sa.String(32), nullable=False),
        sa.Column("adaptive_state_model_version", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("original_regime", sa.String(32), nullable=True),
        sa.Column("current_regime", sa.String(32), nullable=True),
        sa.Column("state_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_r", sa.Float, nullable=True),
        sa.Column("max_achieved_r", sa.Float, nullable=True),
        sa.Column("min_achieved_r", sa.Float, nullable=True),
        sa.Column("elapsed_seconds", sa.Float, nullable=True),
        sa.Column("is_at_or_beyond_breakeven", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_trailing_action", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("milestone_label", sa.String(32), nullable=False),
        sa.Column("giveback_from_mfe_r", sa.Float, nullable=True),
        sa.Column("structure_intact", sa.Boolean, nullable=True),
        sa.Column("bos_against_trade", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("choch_against_trade", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mss_against_trade", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("atr_regime", sa.String(32), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("peer_group_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_fingerprint_id", "milestone_label", "adaptive_state_model_version", name="uq_has_fingerprint_milestone_version"),
    )
    for col in ("source_fingerprint_id", "historical_intelligence_version", "adaptive_state_model_version", "strategy_version",
                "canonical_symbol", "broker_symbol", "direction", "strategy", "current_regime", "state_time",
                "milestone_label", "session", "peer_group_hash", "created_at"):
        _create_index_if_missing(inspector, f"ix_has_{col}", "historical_adaptive_states", [col])
    _create_index_if_missing(inspector, "ix_has_strategy_symbol_dir", "historical_adaptive_states", ["strategy", "canonical_symbol", "direction"])

    _create_table_if_missing(
        inspector, "historical_adaptive_outcomes",
        sa.Column("outcome_id", sa.String(160), primary_key=True),
        sa.Column("state_id", sa.String(160), nullable=False),
        sa.Column("post_exit_status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("post_exit_reached_original_tp", sa.Boolean, nullable=True),
        sa.Column("post_exit_reached_plus_1r", sa.Boolean, nullable=True),
        sa.Column("post_exit_reversed_strongly", sa.Boolean, nullable=True),
        sa.Column("post_exit_would_have_hit_original_sl", sa.Boolean, nullable=True),
        sa.Column("post_exit_mfe_r", sa.Float, nullable=True),
        sa.Column("post_exit_mae_r", sa.Float, nullable=True),
        sa.Column("post_exit_additional_r_available", sa.Float, nullable=True),
        sa.Column("post_exit_time_to_continuation_seconds", sa.Integer, nullable=True),
        sa.Column("post_exit_time_to_reversal_seconds", sa.Integer, nullable=True),
        sa.Column("post_exit_classification", sa.String(32), nullable=True),
        sa.Column("reached_plus_0_25r_additional", sa.Boolean, nullable=True),
        sa.Column("reached_plus_0_5r_additional", sa.Boolean, nullable=True),
        sa.Column("reached_plus_0_75r_additional", sa.Boolean, nullable=True),
        sa.Column("reached_plus_1_5r_additional", sa.Boolean, nullable=True),
        sa.Column("reached_plus_2r_additional", sa.Boolean, nullable=True),
        sa.Column("round_trip_to_breakeven", sa.Boolean, nullable=True),
        sa.Column("round_trip_to_loss", sa.Boolean, nullable=True),
        sa.Column("final_r", sa.Float, nullable=True),
        sa.Column("data_quality", sa.String(24), nullable=True),
        sa.Column("bars_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("state_id", "post_exit_status", "created_at"):
        _create_index_if_missing(inspector, f"ix_hao_{col}", "historical_adaptive_outcomes", [col])


def downgrade() -> None:
    op.drop_table("historical_adaptive_outcomes")
    op.drop_table("historical_adaptive_states")

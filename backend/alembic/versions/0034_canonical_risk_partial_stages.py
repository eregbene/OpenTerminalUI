"""canonical monetary risk (immutable original + mutable current) and persistent partial-profit
stage machine

Revision ID: 0034_canonical_risk_partial_stages
Revises: 0033_engineering_contamination_flag
Create Date: 2026-08-07

Additive only -- no historical migration edited, no column dropped/renamed. Adds:

- adaptive_position_states: original_entry/original_stop_distance/original_risk_money/
  original_risk_pct/original_risk_source (immutable, set once at first sight),
  current_risk_money/protected_profit_money/remaining_open_risk_money (mutable, recomputed every
  cycle), r_source (which basis R was computed on), partial_profit_stage (denormalized current
  stage)
- adaptive_partial_profit_stages: new table, the durable per-attempt record backing the
  partial-profit stage machine (distinct from the existing adaptive_partial_exit_stages table,
  which tracks the separate TP-progress-zone-based partial system)
- mt5_risk_metadata_mismatches: new table, the audit record of every canonical-risk-calculator
  disagreement beyond the configured warning threshold (backend/brokers/mt5/risk_calculator.py)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_canonical_risk_partial_stages"
down_revision = "0033_engineering_contamination_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- adaptive_position_states: canonical monetary risk ---
    op.add_column("adaptive_position_states", sa.Column("original_entry", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("original_stop_distance", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("original_risk_money", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("original_risk_pct", sa.Float(), nullable=True))
    op.add_column("adaptive_position_states", sa.Column("original_risk_source", sa.String(32), nullable=True))
    op.create_index("ix_adaptive_position_states_original_risk_source", "adaptive_position_states", ["original_risk_source"])

    op.add_column("adaptive_position_states", sa.Column("current_risk_money", sa.Float(), nullable=False, server_default="0"))
    op.add_column("adaptive_position_states", sa.Column("protected_profit_money", sa.Float(), nullable=False, server_default="0"))
    op.add_column("adaptive_position_states", sa.Column("remaining_open_risk_money", sa.Float(), nullable=False, server_default="0"))

    op.add_column("adaptive_position_states", sa.Column("r_source", sa.String(32), nullable=False, server_default="UNAVAILABLE"))
    op.create_index("ix_adaptive_position_states_r_source", "adaptive_position_states", ["r_source"])

    op.add_column("adaptive_position_states", sa.Column("partial_profit_stage", sa.String(32), nullable=False, server_default="NONE"))
    op.create_index("ix_adaptive_position_states_partial_profit_stage", "adaptive_position_states", ["partial_profit_stage"])

    # --- adaptive_partial_profit_stages: new table ---
    op.create_table(
        "adaptive_partial_profit_stages",
        sa.Column("stage_attempt_id", sa.String(160), primary_key=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("broker_ticket", sa.String(64), nullable=False),
        sa.Column("account_fingerprint", sa.String(64), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("requested_fraction", sa.Float(), nullable=True),
        sa.Column("requested_volume", sa.Float(), nullable=True),
        sa.Column("executed_volume", sa.Float(), nullable=True),
        sa.Column("broker_deal_id", sa.String(64), nullable=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remaining_broker_volume", sa.Float(), nullable=True),
        sa.Column("reconciliation_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_adaptive_partial_profit_stages_position_id", "adaptive_partial_profit_stages", ["position_id"])
    op.create_index("ix_adaptive_partial_profit_stages_broker_ticket", "adaptive_partial_profit_stages", ["broker_ticket"])
    op.create_index("ix_adaptive_partial_profit_stages_account_fingerprint", "adaptive_partial_profit_stages", ["account_fingerprint"])
    op.create_index("ix_adaptive_partial_profit_stages_stage", "adaptive_partial_profit_stages", ["stage"])
    op.create_index("ix_adaptive_partial_profit_stages_idempotency_key", "adaptive_partial_profit_stages", ["idempotency_key"], unique=True)
    op.create_index("ix_adaptive_partial_profit_stages_reconciliation_status", "adaptive_partial_profit_stages", ["reconciliation_status"])
    op.create_index("ix_adaptive_partial_profit_stages_created_at", "adaptive_partial_profit_stages", ["created_at"])

    # --- mt5_risk_metadata_mismatches: new table ---
    op.create_table(
        "mt5_risk_metadata_mismatches",
        sa.Column("mismatch_id", sa.String(96), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("account_fingerprint", sa.String(64), nullable=True),
        sa.Column("selected_method", sa.String(32), nullable=True),
        sa.Column("selected_loss_per_lot", sa.Float(), nullable=True),
        sa.Column("estimates", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("disagreement_pct", sa.Float(), nullable=True),
        sa.Column("critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_entry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("context", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt5_risk_metadata_mismatches_symbol", "mt5_risk_metadata_mismatches", ["symbol"])
    op.create_index("ix_mt5_risk_metadata_mismatches_account_fingerprint", "mt5_risk_metadata_mismatches", ["account_fingerprint"])
    op.create_index("ix_mt5_risk_metadata_mismatches_disagreement_pct", "mt5_risk_metadata_mismatches", ["disagreement_pct"])
    op.create_index("ix_mt5_risk_metadata_mismatches_critical", "mt5_risk_metadata_mismatches", ["critical"])
    op.create_index("ix_mt5_risk_metadata_mismatches_context", "mt5_risk_metadata_mismatches", ["context"])
    op.create_index("ix_mt5_risk_metadata_mismatches_created_at", "mt5_risk_metadata_mismatches", ["created_at"])


def downgrade() -> None:
    op.drop_table("mt5_risk_metadata_mismatches")
    op.drop_table("adaptive_partial_profit_stages")

    op.drop_index("ix_adaptive_position_states_partial_profit_stage", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "partial_profit_stage")
    op.drop_index("ix_adaptive_position_states_r_source", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "r_source")
    op.drop_column("adaptive_position_states", "remaining_open_risk_money")
    op.drop_column("adaptive_position_states", "protected_profit_money")
    op.drop_column("adaptive_position_states", "current_risk_money")
    op.drop_index("ix_adaptive_position_states_original_risk_source", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "original_risk_source")
    op.drop_column("adaptive_position_states", "original_risk_pct")
    op.drop_column("adaptive_position_states", "original_risk_money")
    op.drop_column("adaptive_position_states", "original_stop_distance")
    op.drop_column("adaptive_position_states", "original_entry")

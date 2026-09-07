"""bsi_thesis_records: canonical BSI thesis/fingerprint companion table (BSI Intelligence
Migration Phase B)

Revision ID: 0075_bsi_thesis_records
Revises: 0074_mt5_memory_snapshot_account_scoped_unique
Create Date: 2026-09-01

Purely additive: creates one new table, `bsi_thesis_records`, one-to-one with the existing
`historical_pattern_fingerprints` table via a `fingerprint_id` foreign key. No existing table is
touched, no existing column is altered, no data migration is performed. Zero live-trading impact:
this table is not read or written by any code on the live signal-generation or execution path as
of this revision -- see backend/historical_intelligence/bsi_canonical_fingerprint.py's own module
docstring for the full design rationale (closes the long-standing gap where bsi_engine.py's own
already-computed `bsi_thesis` metadata dict was never persisted anywhere retrievable).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_bsi_thesis_records"
down_revision: Union[str, Sequence[str], None] = "0074_mt5_memory_snapshot_account_scoped_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "bsi_thesis_records"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return  # idempotent -- safe to re-run if a prior attempt partially applied
    op.create_table(
        _TABLE,
        sa.Column("record_id", sa.String(160), primary_key=True),
        sa.Column("fingerprint_id", sa.String(160), sa.ForeignKey("historical_pattern_fingerprints.fingerprint_id"), nullable=False, unique=True),
        sa.Column("schema_version", sa.String(24), nullable=False, server_default="BSI_THESIS_V1"),
        # IDENTITY
        sa.Column("bsi_version", sa.String(32), nullable=False),
        sa.Column("subtype", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("candidate_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_timeframe", sa.String(16), nullable=False, server_default="M15"),
        # STRUCTURE
        sa.Column("structure_direction", sa.String(16), nullable=True),
        sa.Column("external_structure", sa.String(16), nullable=True),
        sa.Column("internal_structure", sa.String(32), nullable=True),
        sa.Column("protected_high", sa.Float, nullable=True),
        sa.Column("protected_low", sa.Float, nullable=True),
        sa.Column("structure_break_level", sa.Float, nullable=True),
        sa.Column("mss_level", sa.Float, nullable=True),
        # LIQUIDITY
        sa.Column("liquidity_source", sa.String(48), nullable=True),
        sa.Column("liquidity_side", sa.String(16), nullable=True),
        sa.Column("liquidity_level", sa.Float, nullable=True),
        sa.Column("liquidity_swept", sa.Integer, nullable=True),
        sa.Column("sweep_time", sa.DateTime(timezone=True), nullable=True),
        # LOCATION
        sa.Column("dealing_range_low", sa.Float, nullable=True),
        sa.Column("dealing_range_high", sa.Float, nullable=True),
        sa.Column("equilibrium", sa.Float, nullable=True),
        sa.Column("premium_discount_location", sa.String(16), nullable=True),
        # FVG
        sa.Column("fvg_id", sa.String(96), nullable=True),
        sa.Column("fvg_low", sa.Float, nullable=True),
        sa.Column("fvg_high", sa.Float, nullable=True),
        # ORDER BLOCK
        sa.Column("order_block_id", sa.String(96), nullable=True),
        sa.Column("order_block_low", sa.Float, nullable=True),
        sa.Column("order_block_high", sa.Float, nullable=True),
        # SESSION
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("session_window", sa.String(64), nullable=True),
        sa.Column("entry_trigger", sa.String(96), nullable=True),
        # SETUP
        sa.Column("subtype_extension", sa.JSON, nullable=False, server_default="{}"),
        # GEOMETRY
        sa.Column("entry", sa.Float, nullable=False),
        sa.Column("initial_stop", sa.Float, nullable=False),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_level", sa.Float, nullable=True),
        sa.Column("atr_at_entry", sa.Float, nullable=True),
        sa.Column("spread_at_entry", sa.Float, nullable=True),
        # INTELLIGENCE
        sa.Column("hi_result", sa.String(32), nullable=True),
        sa.Column("hi_effective_sample_size", sa.Float, nullable=True),
        sa.Column("hi_historical_expectancy", sa.Float, nullable=True),
        sa.Column("confidence_version", sa.String(32), nullable=True),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("confidence_components", sa.JSON, nullable=True),
        sa.Column("eligibility_decision", sa.String(32), nullable=True),
        sa.Column("rejection_reason", sa.String(64), nullable=True),
        sa.Column("source_rule_ids", sa.JSON, nullable=False, server_default="[]"),
        # POST_ENTRY
        sa.Column("management_policy_version", sa.String(32), nullable=True),
        sa.Column("thesis_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thesis_invalidation_reason", sa.String(64), nullable=True),
        sa.Column("management_actions_applied", sa.JSON, nullable=True),
        # OUTCOME
        sa.Column("mfe_r", sa.Float, nullable=True),
        sa.Column("mae_r", sa.Float, nullable=True),
        sa.Column("baseline_r", sa.Float, nullable=True),
        sa.Column("realized_r", sa.Float, nullable=True),
        sa.Column("exit_reason", sa.String(32), nullable=True),
        sa.Column("post_exit_continuation_r", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bsi_thesis_fingerprint_id", _TABLE, ["fingerprint_id"])
    op.create_index("ix_bsi_thesis_bsi_version", _TABLE, ["bsi_version"])
    op.create_index("ix_bsi_thesis_subtype", _TABLE, ["subtype"])
    op.create_index("ix_bsi_thesis_canonical_symbol", _TABLE, ["canonical_symbol"])
    op.create_index("ix_bsi_thesis_direction", _TABLE, ["direction"])
    op.create_index("ix_bsi_thesis_candidate_time", _TABLE, ["candidate_time"])
    op.create_index("ix_bsi_thesis_entry_time", _TABLE, ["entry_time"])
    op.create_index("ix_bsi_thesis_schema_version", _TABLE, ["schema_version"])
    op.create_index("ix_bsi_thesis_created_at", _TABLE, ["created_at"])
    op.create_index("ix_bsi_thesis_subtype_symbol_direction", _TABLE, ["subtype", "canonical_symbol", "direction"])
    op.create_index("ix_bsi_thesis_subtype_session", _TABLE, ["subtype", "session"])


def downgrade() -> None:
    op.drop_table(_TABLE)

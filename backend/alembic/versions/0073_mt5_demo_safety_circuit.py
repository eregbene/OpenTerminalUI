"""mt5_demo_safety_circuit_state: automatic DEMO strategy demotion trip state

Revision ID: 0073_mt5_demo_safety_circuit
Revises: 0072_adaptive_position_structural_reference
Create Date: 2026-08-25

Bensim -- Adaptive Manager V3 continuation, Part 9: one row per strategy, persisting whether
the automatic DEMO safety circuit (backend/mt5_strategies/demo_safety_circuit.py) has tripped
that strategy to SHADOW_MT5 due to real, sustained, materially-bad DEMO performance evidence.
Deliberately separate from strategy_lifecycle_state (human-approval-gated) and from the
operational circuit_breaker (malfunction-only) -- this is the third, genuinely automatic layer.
New table, no existing data to migrate.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073_mt5_demo_safety_circuit"
down_revision: Union[str, Sequence[str], None] = "0072_adaptive_position_structural_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mt5_demo_safety_circuit_state"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("strategy_id", sa.String(64), primary_key=True),
        sa.Column("tripped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tripped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tripped_reason", sa.String(2000), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("expectancy_r", sa.Float(), nullable=True),
        sa.Column("profit_factor", sa.Float(), nullable=True),
        sa.Column("max_drawdown_r", sa.Float(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_by", sa.String(120), nullable=True),
    )
    op.create_index("ix_mt5_demo_safety_circuit_state_tripped", _TABLE, ["tripped"])


def downgrade() -> None:
    op.drop_index("ix_mt5_demo_safety_circuit_state_tripped", table_name=_TABLE)
    op.drop_table(_TABLE)

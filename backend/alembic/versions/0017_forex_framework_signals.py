"""forex framework signals

Revision ID: 0017_forex_framework_signals
Revises: 0016_forex_feature_store
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_forex_framework_signals"
down_revision = "0016_forex_feature_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forex_framework_signals",
        sa.Column("framework_signal_id", sa.String(120), primary_key=True),
        sa.Column("framework_id", sa.String(80), nullable=False, index=True),
        sa.Column("framework_version", sa.String(32), nullable=False, index=True),
        sa.Column("symbol", sa.String(16), nullable=False, index=True),
        sa.Column("timeframe", sa.String(8), nullable=False, index=True),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("feature_vector_id", sa.String(96), nullable=True, index=True),
        sa.Column("source_dataset_id", sa.String(160), nullable=True, index=True),
        sa.Column("bias", sa.String(32), nullable=False, index=True),
        sa.Column("signal_type", sa.String(80), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("content_hash", sa.String(128), nullable=False, index=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("signal_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("framework_id", "framework_version", "symbol", "timeframe", "analysis_timestamp", "feature_vector_id", name="uq_forex_framework_signal_version"),
    )
    op.create_index("ix_forex_framework_signal_symbol_tf", "forex_framework_signals", ["symbol", "timeframe", "analysis_timestamp"])


def downgrade() -> None:
    op.drop_table("forex_framework_signals")

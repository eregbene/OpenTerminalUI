"""forex feature store

Revision ID: 0016_forex_feature_store
Revises: 0015_phase12_1_persistence_hardening
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_forex_feature_store"
down_revision = "0015_phase12_1_persistence_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forex_feature_vectors",
        sa.Column("feature_vector_id", sa.String(96), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False, index=True),
        sa.Column("timeframe", sa.String(8), nullable=False, index=True),
        sa.Column("candle_timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("candle_content_hash", sa.String(128), nullable=False, index=True),
        sa.Column("feature_version", sa.String(32), nullable=False, index=True),
        sa.Column("engine_version", sa.String(32), nullable=False, index=True),
        sa.Column("source_provider", sa.String(64), nullable=False, index=True),
        sa.Column("source_dataset_id", sa.String(160), nullable=False, index=True),
        sa.Column("source_candle_id", sa.String(160), nullable=False, index=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("completeness", sa.String(32), nullable=False, index=True),
        sa.Column("quality_status", sa.String(32), nullable=False, index=True),
        sa.Column("feature_payload", sa.JSON(), nullable=False),
        sa.Column("structure_payload", sa.JSON(), nullable=False),
        sa.Column("liquidity_payload", sa.JSON(), nullable=False),
        sa.Column("volatility_payload", sa.JSON(), nullable=False),
        sa.Column("trend_payload", sa.JSON(), nullable=False),
        sa.Column("momentum_payload", sa.JSON(), nullable=False),
        sa.Column("session_payload", sa.JSON(), nullable=False),
        sa.Column("regime_payload", sa.JSON(), nullable=False),
        sa.Column("confluence_payload", sa.JSON(), nullable=False),
        sa.Column("explanation_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("symbol", "timeframe", "candle_timestamp", "feature_version", "source_dataset_id", name="uq_forex_feature_vector_version"),
    )
    op.create_index("ix_forex_features_symbol_tf_time", "forex_feature_vectors", ["symbol", "timeframe", "candle_timestamp"])


def downgrade() -> None:
    op.drop_table("forex_feature_vectors")

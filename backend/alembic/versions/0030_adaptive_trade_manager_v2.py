"""adaptive trade manager v2: account isolation, risk sizing, stop/TP quality, dynamic TP

Revision ID: 0030_adaptive_trade_manager_v2
Revises: 0029_economic_intelligence_hardening
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_adaptive_trade_manager_v2"
down_revision = "0029_economic_intelligence_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MT5 account fingerprint/registry -- new 10K demo account (and any future account) gets
    # isolated risk/approval state instead of silently inheriting a previous account's.
    op.create_table(
        "mt5_account_profiles",
        sa.Column("fingerprint_hash", sa.String(64), primary_key=True),
        # BigInteger: some brokers issue demo logins above the 32-bit signed range.
        sa.Column("login", sa.BigInteger(), nullable=False),
        sa.Column("server", sa.String(128), nullable=False),
        sa.Column("company", sa.String(128), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("classification", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("profile_label", sa.String(64), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt5_account_profiles_classification", "mt5_account_profiles", ["classification"])
    op.create_index("ix_mt5_account_profiles_login_server", "mt5_account_profiles", ["login", "server"])

    # Account-fingerprint columns for isolation -- re-verified live every monitor cycle.
    op.add_column("adaptive_management_activations", sa.Column("account_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_adaptive_management_activations_account_fingerprint", "adaptive_management_activations", ["account_fingerprint"])
    op.add_column("adaptive_position_states", sa.Column("account_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_adaptive_position_states_account_fingerprint", "adaptive_position_states", ["account_fingerprint"])

    # v2 stop-quality classification (VALID/TOO_TIGHT/TOO_WIDE/INVALID_STRUCTURE/
    # INVALID_BROKER_DISTANCE/UNAFFORDABLE_RISK) -- additive alongside the existing
    # flags-based `classification`/`stop_quality_classification` columns, which are unchanged.
    op.add_column("adaptive_position_states", sa.Column("stop_quality_v2", sa.String(32), nullable=True))
    op.create_index("ix_adaptive_position_states_stop_quality_v2", "adaptive_position_states", ["stop_quality_v2"])
    op.add_column("adaptive_stop_quality_audits", sa.Column("classification_v2", sa.String(32), nullable=True))
    op.create_index("ix_adaptive_stop_quality_audits_classification_v2", "adaptive_stop_quality_audits", ["classification_v2"])

    # Enforces activation.maximum_actions_per_hour, which previously existed as a stored column
    # that nothing ever read or incremented.
    op.add_column("adaptive_circuit_breakers", sa.Column("actions_hour_window_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("adaptive_circuit_breakers", "actions_hour_window_started_at")

    op.drop_index("ix_adaptive_stop_quality_audits_classification_v2", table_name="adaptive_stop_quality_audits")
    op.drop_column("adaptive_stop_quality_audits", "classification_v2")
    op.drop_index("ix_adaptive_position_states_stop_quality_v2", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "stop_quality_v2")

    op.drop_index("ix_adaptive_position_states_account_fingerprint", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "account_fingerprint")
    op.drop_index("ix_adaptive_management_activations_account_fingerprint", table_name="adaptive_management_activations")
    op.drop_column("adaptive_management_activations", "account_fingerprint")

    op.drop_index("ix_mt5_account_profiles_login_server", table_name="mt5_account_profiles")
    op.drop_index("ix_mt5_account_profiles_classification", table_name="mt5_account_profiles")
    op.drop_table("mt5_account_profiles")

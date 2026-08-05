"""adaptive demo active management

Revision ID: 0024_adaptive_demo_active_management
Revises: 0023_adaptive_trade_management
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_adaptive_demo_active_management"
down_revision = "0023_adaptive_trade_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adaptive_management_activations",
        sa.Column("activation_id", sa.String(128), primary_key=True),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="shadow"),
        sa.Column("demo_account", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False, server_default="local_env"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_strategies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("eligible_symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("maximum_actions_per_hour", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("rollback_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("emergency_state", sa.String(32), nullable=False, server_default="normal"),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_management_activations", ["policy_id", "policy_version", "mode", "demo_account", "effective_from", "approved_at", "emergency_state", "active", "created_at"])

    op.create_table(
        "adaptive_position_states",
        sa.Column("position_id", sa.String(96), primary_key=True),
        sa.Column("activation_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("broker_ticket", sa.String(64), nullable=False),
        sa.Column("thesis_id", sa.String(128), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("original_sl", sa.Float(), nullable=True),
        sa.Column("current_sl", sa.Float(), nullable=True),
        sa.Column("original_tp", sa.Float(), nullable=True),
        sa.Column("current_tp", sa.Float(), nullable=True),
        sa.Column("max_achieved_r", sa.Float(), nullable=False, server_default="0"),
        sa.Column("min_achieved_r", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mfe_price", sa.Float(), nullable=True),
        sa.Column("mae_price", sa.Float(), nullable=True),
        sa.Column("last_management_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_modifications_last_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("managed_automatically", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_position_states", ["activation_id", "symbol", "direction", "broker_ticket", "thesis_id", "opened_at", "last_management_at", "adopted", "managed_automatically", "created_at"])

    op.create_table(
        "adaptive_management_actions",
        sa.Column("action_id", sa.String(160), primary_key=True),
        sa.Column("activation_id", sa.String(128), nullable=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("thesis_id", sa.String(128), nullable=True),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="selected"),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("requested_volume", sa.Float(), nullable=True),
        sa.Column("requested_sl", sa.Float(), nullable=True),
        sa.Column("requested_tp", sa.Float(), nullable=True),
        sa.Column("requested_price", sa.Float(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("considered_actions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_mutation_attempted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_management_actions", ["activation_id", "position_id", "thesis_id", "policy_id", "action_type", "priority", "mode", "status", "idempotency_key", "selected", "expires_at", "broker_mutation_attempted", "created_at"])

    op.create_table(
        "adaptive_broker_action_results",
        sa.Column("result_id", sa.String(160), primary_key=True),
        sa.Column("action_id", sa.String(160), nullable=False),
        sa.Column("broker_ticket", sa.String(64), nullable=True),
        sa.Column("retcode", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("filled_volume", sa.Float(), nullable=True),
        sa.Column("slippage", sa.Float(), nullable=True),
        sa.Column("remaining_volume", sa.Float(), nullable=True),
        sa.Column("confirmed_sl", sa.Float(), nullable=True),
        sa.Column("confirmed_tp", sa.Float(), nullable=True),
        sa.Column("reconciliation_state", sa.String(48), nullable=False, server_default="pending"),
        sa.Column("raw_request", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_broker_action_results", ["action_id", "broker_ticket", "retcode", "status", "reconciliation_state", "created_at"])

    op.create_table(
        "adaptive_circuit_breakers",
        sa.Column("breaker_id", sa.String(96), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="closed"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("failed_actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_this_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_circuit_breakers", ["state", "opened_at", "reset_at", "updated_at"])

    op.create_table(
        "adaptive_position_adoptions",
        sa.Column("adoption_id", sa.String(128), primary_key=True),
        sa.Column("position_id", sa.String(96), nullable=False),
        sa.Column("activation_id", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    _indexes("adaptive_position_adoptions", ["position_id", "activation_id", "approved_at", "active"])

    op.create_table(
        "adaptive_path_reconstruction_jobs",
        sa.Column("job_id", sa.String(160), primary_key=True),
        sa.Column("trade_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("path_version", sa.String(64), nullable=False, server_default="path_reconstruction_v1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_path_reconstruction_jobs", ["trade_id", "session_id", "status", "path_version", "started_at", "completed_at", "created_at"])


def downgrade() -> None:
    for table in (
        "adaptive_path_reconstruction_jobs",
        "adaptive_position_adoptions",
        "adaptive_circuit_breakers",
        "adaptive_broker_action_results",
        "adaptive_management_actions",
        "adaptive_position_states",
        "adaptive_management_activations",
    ):
        op.drop_table(table)


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])

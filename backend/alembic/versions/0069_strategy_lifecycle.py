"""Strategy lifecycle governance: state + audit-history tables

Revision ID: 0069_strategy_lifecycle
Revises: 0068_execution_cost_provenance
Create Date: 2026-08-21

Adds strategy_lifecycle_states (one row per strategy, current lifecycle state) and
strategy_lifecycle_events (append-only audit history of every transition/recommendation).
Purely additive, decision-support only -- see backend/mt5_strategies/lifecycle.py's module
docstring. This is a RICHER, ADDITIVE layer read alongside the existing 3-value
MT5_STRATEGY_ACTIVATION_<ID> mechanism and strategy_performance_recommendations table, not a
replacement for either -- moving a strategy's lifecycle_state to ACTIVE here never itself
changes what executes live; that stays the existing, separate, human-approved env-var deploy.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0069_strategy_lifecycle"
down_revision: Union[str, Sequence[str], None] = "0068_execution_cost_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATES = "strategy_lifecycle_states"
_EVENTS = "strategy_lifecycle_events"


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
        inspector, _STATES,
        sa.Column("strategy_id", sa.String(64), primary_key=True),
        sa.Column("lifecycle_state", sa.String(24), nullable=False),
        sa.Column("mapped_activation", sa.String(24), nullable=False),
        sa.Column("strategy_version_fingerprint", sa.String(64), nullable=True),
        sa.Column("evidence_version_fingerprint", sa.String(64), nullable=True),
        sa.Column("version_status", sa.String(24), nullable=False, server_default="CONSISTENT"),
        sa.Column("pending_recommendation", sa.String(24), nullable=True),
        sa.Column("pending_recommendation_reason", sa.String(2000), nullable=True),
        sa.Column("pending_recommendation_evidence", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("consecutive_negative_periods", sa.Integer, nullable=False, server_default="0"),
        sa.Column("consecutive_positive_periods", sa.Integer, nullable=False, server_default="0"),
        sa.Column("degraded_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_transition_reason", sa.String(2000), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing(inspector, "ix_sls_lifecycle_state", _STATES, ["lifecycle_state"])

    _create_table_if_missing(
        inspector, _EVENTS,
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=True),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("evidence", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("strategy_version_fingerprint", sa.String(64), nullable=True),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing(inspector, "ix_sle_strategy_id", _EVENTS, ["strategy_id"])
    _create_index_if_missing(inspector, "ix_sle_event_type", _EVENTS, ["event_type"])
    _create_index_if_missing(inspector, "ix_sle_created_at", _EVENTS, ["created_at"])


def downgrade() -> None:
    op.drop_table(_EVENTS)
    op.drop_table(_STATES)

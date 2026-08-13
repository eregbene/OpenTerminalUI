"""historical intelligence phase 1/2: ingestion + provider reconciliation + replay + parity

Revision ID: 0051_historical_intelligence_phase1_2
Revises: 0050_post_exit_counterfactual_fields
Create Date: 2026-08-12

New, standalone tables for the Historical Market Intelligence system (backend/
historical_intelligence/). Phase 1 (ingestion/data-quality) and Phase 2 (point-in-time strategy
replay + parity verification against real live candidate evaluations). Does not touch any
existing table -- mt5_canonical_candles (the shared candle store this system ingests into) and
mt5_candidate_evaluations (the live records replay parity is checked against) are read/written
via their existing columns only, no schema change to either.

Idempotent by construction (checks table/index existence before creating): this codebase's
backend.shared.db.init_db() calls Base.metadata.create_all() unconditionally on every app
startup as a safety net alongside Alembic, so any newly-added ORM class can already exist in the
database by the time its own migration runs (observed directly: this migration failed with
DuplicateTableError on first deploy because a test run had already triggered create_all() against
the real database before the container's own entrypoint got to `alembic upgrade head`). Guarding
each create_table/create_index call is the correct fix given that existing app behavior, not a
one-off workaround.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_historical_intelligence_phase1_2"
down_revision: Union[str, Sequence[str], None] = "0050_post_exit_counterfactual_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_table_if_missing(inspector: sa.Inspector, name: str, *columns: sa.Column) -> None:
    if name in inspector.get_table_names():
        return
    op.create_table(name, *columns)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str]) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _create_table_if_missing(
        inspector, "historical_ingestion_runs",
        sa.Column("run_id", sa.String(96), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bars_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bars_persisted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bars_skipped_existing", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bars_flagged_suspect", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bars_flagged_invalid", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gaps_detected", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(24), nullable=False, server_default="RUNNING"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
    )
    for col in ("provider", "canonical_symbol", "broker_symbol", "timeframe", "status", "started_at"):
        _create_index_if_missing(inspector, f"ix_historical_ingestion_runs_{col}", "historical_ingestion_runs", [col])

    _create_table_if_missing(
        inspector, "historical_provider_reconciliations",
        sa.Column("reconciliation_id", sa.String(96), primary_key=True),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("left_provider", sa.String(32), nullable=False),
        sa.Column("right_provider", sa.String(32), nullable=False),
        sa.Column("overlapping_bars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("median_relative_diff", sa.Float, nullable=True),
        sa.Column("mean_relative_diff", sa.Float, nullable=True),
        sa.Column("max_relative_diff", sa.Float, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("canonical_symbol", "timeframe", "status", "created_at"):
        _create_index_if_missing(inspector, f"ix_historical_provider_reconciliations_{col}", "historical_provider_reconciliations", [col])

    _create_table_if_missing(
        inspector, "historical_replay_runs",
        sa.Column("run_id", sa.String(96), primary_key=True),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bars_replayed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidates_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="RUNNING"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
    )
    for col in ("strategy_version", "canonical_symbol", "timeframe", "status", "started_at"):
        _create_index_if_missing(inspector, f"ix_historical_replay_runs_{col}", "historical_replay_runs", [col])

    _create_table_if_missing(
        inspector, "historical_replay_parity_checks",
        sa.Column("check_id", sa.String(96), primary_key=True),
        sa.Column("live_evaluation_id", sa.String(128), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("live_direction", sa.String(16), nullable=True),
        sa.Column("replay_direction", sa.String(16), nullable=True),
        sa.Column("live_entry", sa.Float, nullable=True),
        sa.Column("replay_entry", sa.Float, nullable=True),
        sa.Column("live_stop_loss", sa.Float, nullable=True),
        sa.Column("replay_stop_loss", sa.Float, nullable=True),
        sa.Column("live_confidence", sa.Float, nullable=True),
        sa.Column("replay_confidence", sa.Float, nullable=True),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("diff_detail", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("live_evaluation_id", "canonical_symbol", "strategy_id", "verdict", "created_at"):
        _create_index_if_missing(inspector, f"ix_historical_replay_parity_checks_{col}", "historical_replay_parity_checks", [col])


def downgrade() -> None:
    op.drop_table("historical_replay_parity_checks")
    op.drop_table("historical_replay_runs")
    op.drop_table("historical_provider_reconciliations")
    op.drop_table("historical_ingestion_runs")

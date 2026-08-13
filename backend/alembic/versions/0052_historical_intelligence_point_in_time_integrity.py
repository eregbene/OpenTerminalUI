"""historical intelligence: point-in-time integrity (bar revisions, decision snapshots, UTC fix)

Revision ID: 0052_historical_intelligence_point_in_time_integrity
Revises: 0051_historical_intelligence_phase1_2
Create Date: 2026-08-12

Fixes two confirmed bugs found while verifying Phase 2 replay parity against real live
candidates:

1. Overwrite bug: mt5_canonical_candles rows are upserted (db.merge) on every re-fetch with no
   history kept. Proven directly against this deployment: a single 30-day historical backfill
   stamped the same updated_at across ~364,000 already-live-captured rows, silently replacing
   whatever OHLC the live engine actually decided against. Fixed by mt5_candle_revisions
   (append-only revision history) + a finalized-bar policy (historical_intelligence/quality.py::
   is_finalized) that ingestion.py now respects before overwriting.

2. Broker-clock-vs-UTC bug: MT5 bar timestamps are broker-server time (confirmed empirically:
   true UTC now vs the live bridge's latest tick differed by exactly +3 hours), but were being
   compared directly against true-UTC application timestamps as if the same clock -- this was
   the proximate cause of a regime-classification mismatch during parity testing (replay
   selected a completely different 3-hour-shifted bar window than what live actually used).
   Fixed by mt5_candle_revisions.bar_timestamp_utc (the corrected value every point-in-time
   comparison in replay.py now uses) alongside the new, additive mt5_canonical_candles.
   timestamp_utc / broker_utc_offset_minutes columns.

Also adds mt5_decision_snapshots (Option B): the exact candle slices + derived context the live
engine used for one candidate, captured going forward at generation time -- the authoritative
parity-verification source once populated, sidestepping both bugs above by construction (no
timestamp reconstruction needed at all).

Purely additive: no existing column's type, nullability, or meaning changes; the live trading
path (backend/brokers/mt5/persistence.py) is completely unaffected and continues to leave every
new column at its default. No historical data is rewritten or destroyed.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_historical_intelligence_point_in_time_integrity"
down_revision: Union[str, Sequence[str], None] = "0051_historical_intelligence_phase1_2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_table_if_missing(inspector: sa.Inspector, name: str, *columns: sa.Column) -> None:
    if name in inspector.get_table_names():
        return
    op.create_table(name, *columns)


def _create_index_if_missing(inspector: sa.Inspector, index_name: str, table_name: str, columns: list[str], **kwargs) -> None:
    existing = {ix["name"] for ix in inspector.get_indexes(table_name)} if table_name in inspector.get_table_names() else set()
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name in existing:
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _add_column_if_missing(inspector, "mt5_canonical_candles", sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(inspector, "mt5_canonical_candles", sa.Column("broker_utc_offset_minutes", sa.Integer, nullable=True))
    _add_column_if_missing(inspector, "mt5_canonical_candles", sa.Column("finalized", sa.Boolean, nullable=False, server_default=sa.false()))
    _create_index_if_missing(inspector, "ix_mt5_canonical_candles_finalized", "mt5_canonical_candles", ["finalized"])

    _create_table_if_missing(
        inspector, "mt5_candle_revisions",
        sa.Column("revision_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bar_timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_utc_offset_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("tick_volume", sa.Integer, nullable=False, server_default="0"),
        sa.Column("spread", sa.Integer, nullable=False, server_default="0"),
        sa.Column("real_volume", sa.Integer, nullable=False, server_default="0"),
        sa.Column("finalized", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("post_finalization_anomaly", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "broker_symbol", "timeframe", "bar_timestamp", "revision_number", name="uq_mt5_candle_revision"),
    )
    for col in ("provider", "canonical_symbol", "broker_symbol", "timeframe", "bar_timestamp", "observed_at", "finalized", "post_finalization_anomaly", "created_at"):
        _create_index_if_missing(inspector, f"ix_mt5_candle_revisions_{col}", "mt5_candle_revisions", [col])
    _create_index_if_missing(inspector, "ix_mt5_candle_revision_lookup", "mt5_candle_revisions", ["provider", "broker_symbol", "timeframe", "bar_timestamp", "observed_at"])

    _create_table_if_missing(
        inspector, "mt5_decision_snapshots",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("evaluation_id", sa.String(128), nullable=False),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m15_rows", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("h1_rows", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("h4_rows", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("bid", sa.Float, nullable=True),
        sa.Column("ask", sa.Float, nullable=True),
        sa.Column("spread", sa.Float, nullable=True),
        sa.Column("regime", sa.String(64), nullable=True),
        sa.Column("smc_evidence", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("symbol_info", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_id", name="uq_mt5_decision_snapshot_evaluation"),
    )
    for col in ("evaluation_id", "cycle_id", "account_id", "canonical_symbol", "broker_symbol", "decision_at", "created_at"):
        _create_index_if_missing(inspector, f"ix_mt5_decision_snapshots_{col}", "mt5_decision_snapshots", [col])


def downgrade() -> None:
    op.drop_table("mt5_decision_snapshots")
    op.drop_table("mt5_candle_revisions")
    op.drop_index("ix_mt5_canonical_candles_finalized", table_name="mt5_canonical_candles")
    op.drop_column("mt5_canonical_candles", "finalized")
    op.drop_column("mt5_canonical_candles", "broker_utc_offset_minutes")
    op.drop_column("mt5_canonical_candles", "timestamp_utc")

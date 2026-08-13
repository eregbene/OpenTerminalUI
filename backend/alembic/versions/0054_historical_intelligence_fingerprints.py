"""historical intelligence: pattern fingerprints, outcomes, per-strategy replay trust

Revision ID: 0054_historical_intelligence_fingerprints
Revises: 0053_market_data_as_of
Create Date: 2026-08-13

Phase 3/4: adds historical_pattern_fingerprints (one row per replayed historical setup, Part 1/2
-- versioned via historical_intelligence_version/strategy_version/fingerprint_version, peer-
groupable via peer_group_hash), historical_setup_outcomes (Part 3 -- forward-only outcome
labeling, one row per fingerprint), and strategy_replay_trust (Part 6 -- per-strategy Historical
Intelligence trust state, kept separate from pattern-level statistical reliability).

Purely additive, no existing table/column changes. Idempotent (checks existence before every
create), matching the established convention since migration 0051's crash-loop incident (see that
migration's docstring): backend/shared/db.py::init_db() calls Base.metadata.create_all()
unconditionally on every app startup as a safety net, which can beat this migration to creating
these tables if a test/import path loads the ORM module first.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054_historical_intelligence_fingerprints"
down_revision: Union[str, Sequence[str], None] = "0053_market_data_as_of"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        inspector, "historical_pattern_fingerprints",
        sa.Column("fingerprint_id", sa.String(160), primary_key=True),
        sa.Column("source_evaluation_id", sa.String(128), nullable=True),
        sa.Column("replay_run_id", sa.String(96), nullable=True),
        sa.Column("historical_intelligence_version", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("fingerprint_version", sa.String(32), nullable=False),
        sa.Column("source_quality_tier", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("proxy", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("anchor_strategy", sa.String(64), nullable=False),
        sa.Column("contributing_strategies", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("strategy_family", sa.String(64), nullable=True),
        sa.Column("regime", sa.String(64), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("confidence_band", sa.String(32), nullable=True),
        sa.Column("m15_trend", sa.String(16), nullable=True),
        sa.Column("h1_trend", sa.String(16), nullable=True),
        sa.Column("h4_trend", sa.String(16), nullable=True),
        sa.Column("bos_present", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("choch_present", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mss_present", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("displacement_present", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("liquidity_sweep_present", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("liquidity_location", sa.String(16), nullable=False, server_default="none"),
        sa.Column("fvg_state", sa.String(16), nullable=False, server_default="none"),
        sa.Column("order_block_state", sa.String(16), nullable=False, server_default="none"),
        sa.Column("premium_discount_position", sa.String(32), nullable=True),
        sa.Column("support_resistance_context", sa.String(32), nullable=True),
        sa.Column("atr_regime", sa.String(16), nullable=True),
        sa.Column("atr_percentile", sa.Float, nullable=True),
        sa.Column("volatility_regime", sa.String(16), nullable=True),
        sa.Column("spread_regime", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("stop_distance_atr_bucket", sa.String(16), nullable=True),
        sa.Column("planned_rr_bucket", sa.String(16), nullable=True),
        sa.Column("day_of_week", sa.Integer, nullable=True),
        sa.Column("time_of_day_bucket", sa.String(16), nullable=True),
        sa.Column("economic_event_context", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("entry", sa.Float, nullable=False),
        sa.Column("stop_loss", sa.Float, nullable=False),
        sa.Column("take_profit", sa.Float, nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("peer_group_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("source_evaluation_id", "replay_run_id", "historical_intelligence_version", "strategy_version", "fingerprint_version",
                "source_quality_tier", "provider", "canonical_symbol", "direction", "anchor_strategy", "strategy_family",
                "regime", "session", "atr_regime", "peer_group_hash", "entry_time", "created_at"):
        _create_index_if_missing(inspector, f"ix_hpf_{col}", "historical_pattern_fingerprints", [col])
    _create_index_if_missing(inspector, "ix_hpf_peer_group_lookup", "historical_pattern_fingerprints", ["peer_group_hash", "strategy_version", "fingerprint_version"])
    _create_index_if_missing(inspector, "ix_hpf_symbol_strategy", "historical_pattern_fingerprints", ["canonical_symbol", "anchor_strategy"])

    _create_table_if_missing(
        inspector, "historical_setup_outcomes",
        sa.Column("outcome_id", sa.String(160), primary_key=True),
        sa.Column("fingerprint_id", sa.String(160), nullable=False),
        sa.Column("resolution_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("outcome_r", sa.Float, nullable=True),
        sa.Column("net_outcome_r", sa.Float, nullable=True),
        sa.Column("mfe_r", sa.Float, nullable=True),
        sa.Column("mae_r", sa.Float, nullable=True),
        sa.Column("tp_hit", sa.Boolean, nullable=True),
        sa.Column("sl_hit", sa.Boolean, nullable=True),
        sa.Column("reached_0_25r", sa.Boolean, nullable=True),
        sa.Column("reached_0_5r", sa.Boolean, nullable=True),
        sa.Column("reached_0_75r", sa.Boolean, nullable=True),
        sa.Column("reached_1r", sa.Boolean, nullable=True),
        sa.Column("reached_1_5r", sa.Boolean, nullable=True),
        sa.Column("reached_2r", sa.Boolean, nullable=True),
        sa.Column("immediate_failure", sa.Boolean, nullable=True),
        sa.Column("time_to_0_5r_seconds", sa.Float, nullable=True),
        sa.Column("time_to_1r_seconds", sa.Float, nullable=True),
        sa.Column("time_to_mfe_seconds", sa.Float, nullable=True),
        sa.Column("holding_duration_seconds", sa.Float, nullable=True),
        sa.Column("bars_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint_id", name="uq_historical_setup_outcome_fingerprint"),
    )
    _create_index_if_missing(inspector, "ix_hso_fingerprint_id", "historical_setup_outcomes", ["fingerprint_id"])
    _create_index_if_missing(inspector, "ix_hso_resolution_status", "historical_setup_outcomes", ["resolution_status"])
    _create_index_if_missing(inspector, "ix_hso_created_at", "historical_setup_outcomes", ["created_at"])

    _create_table_if_missing(
        inspector, "strategy_replay_trust",
        sa.Column("strategy_id", sa.String(64), primary_key=True),
        sa.Column("trust_state", sa.String(32), nullable=False, server_default="HIST_INTEL_UNTRUSTED_REPLAY"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("exact_match_pct", sa.Float, nullable=True),
        sa.Column("direction_match_pct", sa.Float, nullable=True),
        sa.Column("strategy_presence_pct", sa.Float, nullable=True),
        sa.Column("regime_match_pct", sa.Float, nullable=True),
        sa.Column("snapshot_tier_pct", sa.Float, nullable=True),
        sa.Column("snapshot_tier_sample_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("strategy_version", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing(inspector, "ix_srt_trust_state", "strategy_replay_trust", ["trust_state"])
    _create_index_if_missing(inspector, "ix_srt_computed_at", "strategy_replay_trust", ["computed_at"])


def downgrade() -> None:
    op.drop_table("strategy_replay_trust")
    op.drop_table("historical_setup_outcomes")
    op.drop_table("historical_pattern_fingerprints")

"""adaptive trade management

Revision ID: 0023_adaptive_trade_management
Revises: 0022_mt5_trade_memory
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_adaptive_trade_management"
down_revision = "0022_mt5_trade_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adaptive_trade_sessions",
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="MT5"),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("account_mode", sa.String(16), nullable=False, server_default="DEMO"),
        sa.Column("server", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("imported_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_deals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_trade_sessions", ["source", "account_id", "account_mode", "server", "started_at", "ended_at", "created_at"])

    op.create_table(
        "adaptive_trade_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("trade_id", sa.String(128), nullable=True),
        sa.Column("ticket", sa.String(64), nullable=True),
        sa.Column("order_id", sa.String(64), nullable=True),
        sa.Column("deal_id", sa.String(64), nullable=True),
        sa.Column("position_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("swap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("magic", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=False, server_default="UNKNOWN"),
        sa.Column("strategy_version", sa.String(64), nullable=False, server_default="UNKNOWN"),
        sa.Column("setup_id", sa.String(96), nullable=False, server_default="UNKNOWN"),
        sa.Column("timeframe", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("broker_exit_reason", sa.String(64), nullable=True),
        sa.Column("server_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("utc_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_trade_events", ["session_id", "trade_id", "ticket", "order_id", "deal_id", "position_id", "event_type", "symbol", "side", "magic", "strategy_id", "strategy_version", "setup_id", "timeframe", "broker_exit_reason", "server_time", "utc_time", "actor", "created_at"])
    op.create_index("ix_adaptive_events_symbol_time", "adaptive_trade_events", ["symbol", "utc_time"])
    op.create_index("ix_adaptive_events_trade_time", "adaptive_trade_events", ["trade_id", "utc_time"])

    op.create_table(
        "adaptive_trade_theses",
        sa.Column("thesis_id", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False, server_default="UNKNOWN"),
        sa.Column("setup_id", sa.String(96), nullable=False, server_default="UNKNOWN"),
        sa.Column("timeframe", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("signal_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_regime", sa.String(64), nullable=False, server_default="insufficient_data"),
        sa.Column("relationship_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("trade_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("trades_per_thesis", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("peak_exposure", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_entry_result", sa.Float(), nullable=True),
        sa.Column("additional_entry_contribution", sa.Float(), nullable=False, server_default="0"),
        sa.Column("extra_entries_helped", sa.Boolean(), nullable=True),
        sa.Column("correlation_score", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_trade_theses", ["session_id", "symbol", "direction", "strategy_id", "setup_id", "timeframe", "signal_timestamp", "market_regime", "trades_per_thesis", "extra_entries_helped", "created_at"])
    op.create_index("ix_adaptive_thesis_group", "adaptive_trade_theses", ["symbol", "direction", "strategy_id", "setup_id", "timeframe"])

    op.create_table(
        "adaptive_trade_paths",
        sa.Column("path_id", sa.String(160), primary_key=True),
        sa.Column("trade_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("initial_risk_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("initial_monetary_risk", sa.Float(), nullable=False, server_default="0"),
        sa.Column("initial_target_r", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mfe", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mae", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_achieved_r", sa.Float(), nullable=False, server_default="0"),
        sa.Column("min_achieved_r", sa.Float(), nullable=False, server_default="0"),
        sa.Column("time_to_mfe_seconds", sa.Integer(), nullable=True),
        sa.Column("time_to_mae_seconds", sa.Integer(), nullable=True),
        sa.Column("profit_retracement_from_mfe", sa.Float(), nullable=False, server_default="0"),
        sa.Column("candle_count_held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spread_at_entry", sa.Float(), nullable=True),
        sa.Column("maximum_spread", sa.Float(), nullable=True),
        sa.Column("spread_at_exit", sa.Float(), nullable=True),
        sa.Column("volatility_at_entry", sa.Float(), nullable=True),
        sa.Column("volatility_during_trade", sa.Float(), nullable=True),
        sa.Column("market_regime_entry", sa.String(64), nullable=False, server_default="insufficient_data"),
        sa.Column("market_regime_exit", sa.String(64), nullable=False, server_default="insufficient_data"),
        sa.Column("timeline", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("context_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("adaptive_trade_paths", ["trade_id", "session_id", "symbol", "direction", "market_regime_entry", "market_regime_exit", "created_at"])

    op.create_table("adaptive_market_regime_snapshots", sa.Column("regime_id", sa.String(160), primary_key=True), sa.Column("symbol", sa.String(32), nullable=False), sa.Column("timeframe", sa.String(16), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("regime", sa.String(64), nullable=False), sa.Column("confidence", sa.Float(), nullable=False, server_default="0"), sa.Column("features", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("rule_version", sa.String(64), nullable=False, server_default="deterministic_regime_v1"), sa.Column("previous_regime", sa.String(64), nullable=True), sa.Column("transition", sa.String(96), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("symbol", "timeframe", "timestamp", "rule_version", name="uq_adaptive_regime_symbol_tf_time_version"))
    _indexes("adaptive_market_regime_snapshots", ["symbol", "timeframe", "timestamp", "regime", "rule_version", "previous_regime", "transition", "created_at"])

    op.create_table("adaptive_trade_management_policies", sa.Column("policy_id", sa.String(128), primary_key=True), sa.Column("name", sa.String(128), nullable=False), sa.Column("version", sa.String(32), nullable=False), sa.Column("family", sa.String(64), nullable=False), sa.Column("description", sa.Text(), nullable=False, server_default=""), sa.Column("eligible_strategies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("eligible_symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("eligible_timeframes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("eligible_regimes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("minimum_data_requirements", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("validation_status", sa.String(32), nullable=False, server_default="research"), sa.Column("scorecard", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("parent_policy_id", sa.String(128), nullable=True), sa.Column("evidence_artifacts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True), sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("name", "version", name="uq_adaptive_policy_name_version"))
    _indexes("adaptive_trade_management_policies", ["version", "family", "validation_status", "parent_policy_id", "approved_at", "retired_at", "created_at"])

    op.create_table("adaptive_shadow_decisions", sa.Column("decision_id", sa.String(160), primary_key=True), sa.Column("trade_id", sa.String(128), nullable=False), sa.Column("policy_id", sa.String(128), nullable=False), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("proposed_action", sa.String(64), nullable=False), sa.Column("proposed_volume_fraction", sa.Float(), nullable=False, server_default="0"), sa.Column("proposed_price", sa.Float(), nullable=True), sa.Column("reason", sa.Text(), nullable=False, server_default=""), sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("would_mutate_broker", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    _indexes("adaptive_shadow_decisions", ["trade_id", "policy_id", "timestamp", "proposed_action", "would_mutate_broker", "created_at"])
    op.create_index("ix_adaptive_shadow_trade_policy_time", "adaptive_shadow_decisions", ["trade_id", "policy_id", "timestamp"])

    op.create_table("adaptive_counterfactual_outcomes", sa.Column("outcome_id", sa.String(160), primary_key=True), sa.Column("trade_id", sa.String(128), nullable=False), sa.Column("policy_id", sa.String(128), nullable=False), sa.Column("actual_pnl", sa.Float(), nullable=False, server_default="0"), sa.Column("hypothetical_pnl", sa.Float(), nullable=False, server_default="0"), sa.Column("hypothetical_r", sa.Float(), nullable=False, server_default="0"), sa.Column("hypothetical_exit_time", sa.DateTime(timezone=True), nullable=True), sa.Column("hypothetical_exit_price", sa.Float(), nullable=True), sa.Column("difference_from_actual", sa.Float(), nullable=False, server_default="0"), sa.Column("loss_reduced", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("profit_reduced", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("tp_later_reached", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("sl_later_reached", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("mfe_capture", sa.Float(), nullable=False, server_default="0"), sa.Column("false_early_exit", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("avoided_loss", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("giveback_avoided", sa.Float(), nullable=False, server_default="0"), sa.Column("extra_transaction_costs", sa.Float(), nullable=False, server_default="0"), sa.Column("applicable", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("trade_id", "policy_id", name="uq_adaptive_counterfactual_trade_policy"))
    _indexes("adaptive_counterfactual_outcomes", ["trade_id", "policy_id", "hypothetical_exit_time", "loss_reduced", "profit_reduced", "false_early_exit", "avoided_loss", "applicable", "created_at"])

    op.create_table("adaptive_experiments", sa.Column("experiment_id", sa.String(128), primary_key=True), sa.Column("experiment_type", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="research"), sa.Column("train_period", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("validation_period", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("out_of_sample_period", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("selected_policy_id", sa.String(128), nullable=True), sa.Column("parameter_stability", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("train_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("out_of_sample_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("degradation", sa.Float(), nullable=True), sa.Column("regime_coverage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("reward_definition", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("promotion_requires_manual_approval", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    _indexes("adaptive_experiments", ["experiment_type", "status", "selected_policy_id", "immutable", "created_at"])

    op.create_table("adaptive_champion_challenger_results", sa.Column("result_id", sa.String(160), primary_key=True), sa.Column("experiment_id", sa.String(128), nullable=False), sa.Column("champion_policy_id", sa.String(128), nullable=False), sa.Column("challenger_policy_id", sa.String(128), nullable=False), sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"), sa.Column("champion_score", sa.Float(), nullable=False, server_default="0"), sa.Column("challenger_score", sa.Float(), nullable=False, server_default="0"), sa.Column("recommendation", sa.String(64), nullable=False, server_default="INSUFFICIENT_DATA"), sa.Column("manual_approval_required", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("scorecard", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    _indexes("adaptive_champion_challenger_results", ["experiment_id", "champion_policy_id", "challenger_policy_id", "sample_size", "recommendation", "created_at"])

    op.create_table("adaptive_drift_snapshots", sa.Column("drift_id", sa.String(160), primary_key=True), sa.Column("scope", sa.String(64), nullable=False), sa.Column("symbol", sa.String(32), nullable=True), sa.Column("state", sa.String(32), nullable=False, server_default="insufficient_data"), sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("recommendation", sa.Text(), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    _indexes("adaptive_drift_snapshots", ["scope", "symbol", "state", "created_at"])

    op.create_table("adaptive_session_reports", sa.Column("report_id", sa.String(128), primary_key=True), sa.Column("session_id", sa.String(128), nullable=False), sa.Column("actual_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("baseline_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("challenger_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"), sa.Column("uncertainty", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("recommendation", sa.String(64), nullable=False, server_default="INSUFFICIENT_DATA"), sa.Column("insufficient_data", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    _indexes("adaptive_session_reports", ["session_id", "recommendation", "insufficient_data", "created_at"])


def downgrade() -> None:
    for table in (
        "adaptive_session_reports",
        "adaptive_drift_snapshots",
        "adaptive_champion_challenger_results",
        "adaptive_experiments",
        "adaptive_counterfactual_outcomes",
        "adaptive_shadow_decisions",
        "adaptive_trade_management_policies",
        "adaptive_market_regime_snapshots",
        "adaptive_trade_paths",
        "adaptive_trade_theses",
        "adaptive_trade_events",
        "adaptive_trade_sessions",
    ):
        op.drop_table(table)


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])

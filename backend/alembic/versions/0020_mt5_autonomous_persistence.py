"""mt5 autonomous persistence

Revision ID: 0020_mt5_autonomous_persistence
Revises: 0019_ibkr_record_provenance_and_quarantine
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_mt5_autonomous_persistence"
down_revision = "0019_ibkr_record_provenance_and_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mt5_scheduler_cycles",
        sa.Column("cycle_id", sa.String(96), primary_key=True),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("candle_id", sa.String(96), nullable=True),
        sa.Column("candle_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_policy", sa.String(32), nullable=False, server_default="MT5_ONLY"),
        sa.Column("symbols_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_symbols", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_symbol", sa.String(32), nullable=True),
        sa.Column("selected_candidate_id", sa.String(128), nullable=True),
        sa.Column("ai_decision_id", sa.String(128), nullable=True),
        sa.Column("trade_id", sa.String(128), nullable=True),
        sa.Column("openai_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order_send_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("status", "candle_id", "candle_timestamp", "provider_policy", "selected_symbol", "selected_candidate_id", "ai_decision_id", "trade_id", "created_at"):
        op.create_index(f"ix_mt5_scheduler_cycles_{col}", "mt5_scheduler_cycles", [col])

    op.create_table(
        "mt5_scheduler_candidates",
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("ranking_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("risk_reward", sa.Float(), nullable=True),
        sa.Column("strategy_outputs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("consensus", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("market_regime", sa.String(64), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("timeframe_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("context_hash", sa.String(128), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("cycle_id", "symbol", "broker_symbol", "asset_class", "direction", "rejected", "risk_reward", "market_regime", "session", "context_hash", "created_at"):
        op.create_index(f"ix_mt5_scheduler_candidates_{col}", "mt5_scheduler_candidates", [col])
    op.create_index("ix_mt5_candidates_cycle_score", "mt5_scheduler_candidates", ["cycle_id", "ranking_score"])

    op.create_table(
        "mt5_ai_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("candidate_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(96), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("cycle_id", "candidate_id", "symbol", "decision", "confidence", "model", "created_at"):
        op.create_index(f"ix_mt5_ai_decisions_{col}", "mt5_ai_decisions", [col])

    op.create_table(
        "mt5_order_records",
        sa.Column("order_id", sa.String(128), primary_key=True),
        sa.Column("trade_id", sa.String(128), nullable=True),
        sa.Column("cycle_id", sa.String(96), nullable=True),
        sa.Column("candidate_id", sa.String(128), nullable=True),
        sa.Column("decision_id", sa.String(128), nullable=True),
        sa.Column("intent_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("retcode", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("broker_order_ticket", sa.String(64), nullable=True),
        sa.Column("deal_ticket", sa.String(64), nullable=True),
        sa.Column("requested_volume", sa.Float(), nullable=True),
        sa.Column("filled_volume", sa.Float(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("raw_request", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("raw_response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("trade_id", "cycle_id", "candidate_id", "decision_id", "intent_id", "symbol", "direction", "status", "retcode", "broker_order_ticket", "deal_ticket", "created_at"):
        op.create_index(f"ix_mt5_order_records_{col}", "mt5_order_records", [col])

    op.create_table(
        "mt5_trade_records",
        sa.Column("trade_id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(96), nullable=False),
        sa.Column("candidate_id", sa.String(128), nullable=True),
        sa.Column("ai_decision_id", sa.String(128), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("lot_size", sa.Float(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("projected_margin", sa.Float(), nullable=True),
        sa.Column("projected_risk", sa.Float(), nullable=True),
        sa.Column("risk_reward", sa.Float(), nullable=True),
        sa.Column("order_ticket", sa.String(64), nullable=True),
        sa.Column("deal_tickets", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("current_pnl", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=True),
        sa.Column("swap", sa.Float(), nullable=True),
        sa.Column("open_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("exit_reason", sa.String(64), nullable=True),
        sa.Column("strategy_outputs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("consensus", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("market_regime", sa.String(64), nullable=True),
        sa.Column("session", sa.String(32), nullable=True),
        sa.Column("timeframe_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("broker_server", sa.String(128), nullable=True),
        sa.Column("account_mode", sa.String(16), nullable=False, server_default="DEMO"),
        sa.Column("reconciliation_state", sa.String(48), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("cycle_id", "candidate_id", "ai_decision_id", "symbol", "broker_symbol", "direction", "lot_size", "risk_reward", "order_ticket", "open_timestamp", "close_timestamp", "exit_reason", "market_regime", "session", "broker_server", "account_mode", "reconciliation_state", "created_at"):
        op.create_index(f"ix_mt5_trade_records_{col}", "mt5_trade_records", [col])
    op.create_index("ix_mt5_trades_symbol_open", "mt5_trade_records", ["symbol", "open_timestamp"])
    op.create_index("ix_mt5_trades_perf", "mt5_trade_records", ["symbol", "session", "market_regime", "exit_reason"])

    op.create_table(
        "mt5_canonical_candles",
        sa.Column("candle_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="MT5"),
        sa.Column("dataset_policy", sa.String(32), nullable=False, server_default="MT5_ONLY"),
        sa.Column("canonical_symbol", sa.String(32), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("tick_volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spread", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("real_volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality", sa.String(32), nullable=False, server_default="VALID"),
        sa.Column("delayed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lineage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("broker_server", sa.String(128), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "broker_symbol", "timeframe", "timestamp", name="uq_mt5_candle_provider_symbol_tf_ts"),
    )
    for col in ("provider", "dataset_policy", "canonical_symbol", "broker_symbol", "timeframe", "timestamp", "quality", "delayed", "proxy", "broker_server", "fetched_at", "created_at"):
        op.create_index(f"ix_mt5_canonical_candles_{col}", "mt5_canonical_candles", [col])
    op.create_index("ix_mt5_candles_symbol_tf_time", "mt5_canonical_candles", ["canonical_symbol", "timeframe", "timestamp"])

    op.create_table(
        "mt5_retention_policies",
        sa.Column("policy_id", sa.String(96), primary_key=True),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("entity", "enabled"):
        op.create_index(f"ix_mt5_retention_policies_{col}", "mt5_retention_policies", [col])


def downgrade() -> None:
    for table in (
        "mt5_retention_policies",
        "mt5_canonical_candles",
        "mt5_trade_records",
        "mt5_order_records",
        "mt5_ai_decisions",
        "mt5_scheduler_candidates",
        "mt5_scheduler_cycles",
    ):
        op.drop_table(table)

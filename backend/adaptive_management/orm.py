from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdaptiveSessionORM(Base):
    __tablename__ = "adaptive_trade_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5", index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO", index=True)
    server: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    imported_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_deals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptiveTradeEventORM(Base):
    __tablename__ = "adaptive_trade_events"

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    position_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    swap: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    magic: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN", index=True)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN", index=True)
    setup_id: Mapped[str] = mapped_column(String(96), nullable=False, default="UNKNOWN", index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN", index=True)
    broker_exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    server_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    utc_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN", index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (
        Index("ix_adaptive_events_symbol_time", "symbol", "utc_time"),
        Index("ix_adaptive_events_trade_time", "trade_id", "utc_time"),
    )


class TradeThesisORM(Base):
    __tablename__ = "adaptive_trade_theses"

    thesis_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN", index=True)
    setup_id: Mapped[str] = mapped_column(String(96), nullable=False, default="UNKNOWN", index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN", index=True)
    signal_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    market_regime: Mapped[str] = mapped_column(String(64), nullable=False, default="insufficient_data", index=True)
    relationship_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    trade_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    trades_per_thesis: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    total_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    peak_exposure: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    first_entry_result: Mapped[float | None] = mapped_column(Float, nullable=True)
    additional_entry_contribution: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    extra_entries_helped: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    correlation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_adaptive_thesis_group", "symbol", "direction", "strategy_id", "setup_id", "timeframe"),)


class TradePathSnapshotORM(Base):
    __tablename__ = "adaptive_trade_paths"

    path_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    initial_risk_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    initial_monetary_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    initial_target_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    mfe: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    mae: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_achieved_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    min_achieved_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    time_to_mfe_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_to_mae_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profit_retracement_from_mfe: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    candle_count_held: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spread_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_at_exit: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_during_trade: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime_entry: Mapped[str] = mapped_column(String(64), nullable=False, default="insufficient_data", index=True)
    market_regime_exit: Mapped[str] = mapped_column(String(64), nullable=False, default="insufficient_data", index=True)
    timeline: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MarketRegimeSnapshotORM(Base):
    __tablename__ = "adaptive_market_regime_snapshots"

    regime_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    regime: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="deterministic_regime_v1", index=True)
    previous_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    transition: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp", "rule_version", name="uq_adaptive_regime_symbol_tf_time_version"),)


class TradeManagementPolicyORM(Base):
    __tablename__ = "adaptive_trade_management_policies"

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    eligible_strategies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    eligible_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    eligible_timeframes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    eligible_regimes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    minimum_data_requirements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="research", index=True)
    scorecard: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parent_policy_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    evidence_artifacts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_adaptive_policy_name_version"),)


class ShadowDecisionORM(Base):
    __tablename__ = "adaptive_shadow_decisions"

    decision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    proposed_action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    proposed_volume_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    proposed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    would_mutate_broker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (Index("ix_adaptive_shadow_trade_policy_time", "trade_id", "policy_id", "timestamp"),)


class CounterfactualOutcomeORM(Base):
    __tablename__ = "adaptive_counterfactual_outcomes"

    outcome_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actual_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    hypothetical_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    hypothetical_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    hypothetical_exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    hypothetical_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    difference_from_actual: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    loss_reduced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    profit_reduced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    tp_later_reached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sl_later_reached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfe_capture: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    false_early_exit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    avoided_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    giveback_avoided: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    extra_transaction_costs: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("trade_id", "policy_id", name="uq_adaptive_counterfactual_trade_policy"),)


class AdaptiveExperimentORM(Base):
    __tablename__ = "adaptive_experiments"

    experiment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    experiment_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="research", index=True)
    train_period: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_period: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    out_of_sample_period: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    selected_policy_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    parameter_stability: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    train_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    out_of_sample_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    degradation: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime_coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reward_definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    promotion_requires_manual_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class ChampionChallengerResultORM(Base):
    __tablename__ = "adaptive_champion_challenger_results"

    result_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    champion_policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    challenger_policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    champion_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    challenger_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    recommendation: Mapped[str] = mapped_column(String(64), nullable=False, default="INSUFFICIENT_DATA", index=True)
    manual_approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scorecard: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class DriftSnapshotORM(Base):
    __tablename__ = "adaptive_drift_snapshots"

    drift_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="insufficient_data", index=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AdaptiveSessionReportORM(Base):
    __tablename__ = "adaptive_session_reports"

    report_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actual_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    baseline_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    challenger_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertainty: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recommendation: Mapped[str] = mapped_column(String(64), nullable=False, default="INSUFFICIENT_DATA", index=True)
    insufficient_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AdaptiveActivationORM(Base):
    __tablename__ = "adaptive_management_activations"

    activation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="shadow", index=True)
    demo_account: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False, default="local_env")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    eligible_strategies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    eligible_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    maximum_actions_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    rollback_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    emergency_state: Mapped[str] = mapped_column(String(32), nullable=False, default="normal", index=True)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptivePositionStateORM(Base):
    __tablename__ = "adaptive_position_states"

    position_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    activation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    broker_ticket: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thesis_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    original_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    current_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    original_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_achieved_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    min_achieved_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    mfe_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_management_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    stop_modifications_last_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adopted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    managed_automatically: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    tp_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_tp_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_tp_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_giveback_r: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    winner_classification: Mapped[str] = mapped_column(String(32), nullable=False, default="insufficient_data", index=True)
    stop_quality_classification: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    profit_lock_floor_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    closed_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptiveStopQualityAuditORM(Base):
    __tablename__ = "adaptive_stop_quality_audits"

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, default="UNKNOWN", index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN", index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sl_distance_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sl_atr_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_pct_of_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    structure_buffer_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    broker_min_stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="insufficient_data", index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("position_id", name="uq_adaptive_stop_quality_position"),)


class AdaptivePartialExitStageORM(Base):
    __tablename__ = "adaptive_partial_exit_stages"

    stage_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_tp_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    requested_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    executed_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("position_id", "stage", name="uq_adaptive_partial_stage_position_stage"),)


class AdaptiveManagementActionORM(Base):
    __tablename__ = "adaptive_management_actions"

    action_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    activation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    thesis_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="selected", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    requested_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    considered_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    broker_mutation_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptiveBrokerActionResultORM(Base):
    __tablename__ = "adaptive_broker_action_results"

    result_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    broker_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    retcode: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    reconciliation_state: Mapped[str] = mapped_column(String(48), nullable=False, default="pending", index=True)
    raw_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AdaptiveCircuitBreakerORM(Base):
    __tablename__ = "adaptive_circuit_breakers"

    breaker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="closed", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_this_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptivePositionAdoptionORM(Base):
    __tablename__ = "adaptive_position_adoptions"

    adoption_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    activation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AdaptivePathReconstructionJobORM(Base):
    __tablename__ = "adaptive_path_reconstruction_jobs"

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    path_version: Mapped[str] = mapped_column(String(64), nullable=False, default="path_reconstruction_v1", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

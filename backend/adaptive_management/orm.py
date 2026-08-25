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
    # Which MT5 account profile (account_registry.MT5AccountProfile.account_id, e.g. "demo_10k")
    # this event belongs to -- position_id/trade_id/ticket are only unique WITHIN one account.
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
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
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="shadow", index=True)
    demo_account: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # sha256(login:server) via account_registry.fingerprint_account() -- re-verified against
    # the live account on every monitor cycle so switching MT5 accounts (e.g. onto a new demo
    # account) automatically deactivates this activation instead of silently continuing to
    # manage positions under an activation approved for a different account.
    account_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    # position_id ITSELF is now account-scoped for every account except demo_10k (see
    # backend.adaptive_management.service._position_id) -- demo_10k keeps the raw ticket for
    # backward compatibility with existing rows, so account_id is still required here as the
    # authoritative scoping column (not derivable from position_id alone for that one account).
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    activation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # sha256(login:server) -- scopes state isolation per MT5 account. broker ticket numbers can
    # collide across different accounts/brokers, so this (not just position_id) is what
    # guarantees a new account never inherits a previous account's risk/management state.
    account_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    stop_quality_v2: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    profit_lock_floor_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # Bensim -- Adaptive Manager V3, Part 8 ("the manager should not have to infer the original
    # thesis after the position is already open"). Captured ONCE, in _sync_position_state's
    # is_first_sight branch, from the originating MT5CandidateEvaluationORM row (matched by
    # symbol+direction+nearest creation time) -- same "set once, never overwritten" contract as
    # entry_regime/entry_atr above. None when no matching evaluation row is found (e.g. an
    # adopted/manually-opened position with no candidate-evaluation ancestry) -- never guessed.
    setup_subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    original_target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_sl_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Adaptive Manager V3 continuation, Part 5/6: raw price of the structural level (broken
    # session high/low, broken BOS level, swing reference, ...) the ORIGINATING candidate's own
    # geometry referenced -- see _shared.py::_geometry_metadata's "structural_reference" field.
    # Lets the manager check "has price closed back across the level it originally broke" without
    # re-detecting structure. None when the originating candidate had no structural reference
    # (e.g. an ATR-only stop) -- never fabricated.
    original_structural_reference: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Trade Intelligence Dataset: market conditions at entry, captured once (first sight of the
    # position, before any management has run) and never overwritten afterward -- distinct from
    # the live/current regime evaluated every cycle for management decisions. This is what lets
    # future probability queries group by "the regime the trade was ENTERED in", not the regime
    # it happens to be in right now.
    entry_regime: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    entry_regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Set once this closed trade has been auto-replayed against every named management policy
    # (see AdaptiveManagementService._auto_replay_recently_closed). Stays NULL (not
    # closed_detected_at itself) so a trade whose deal data wasn't backfilled yet on the first
    # attempt is retried on a later cycle instead of being silently skipped forever.
    replay_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Engineering-contamination flag: marks a position whose management history was affected by
    # a since-fixed manager BUG (not a real trading/strategy outcome) -- e.g. the runaway-R
    # incident on ticket 57873187767, which fired erroneous PARTIAL_PROFIT broker closes.
    # Contaminated positions keep their full broker/execution history (nothing here is deleted
    # or hidden from per-ticket lookups -- position_detail/position_history/replay_results all
    # still work normally) but are excluded from aggregate "clean" statistics that would
    # otherwise treat the bug's side effects as real strategy performance: audit_report() (and
    # everything built on it -- probability_report/leaderboard_report/performance), and
    # replay-policy promotion (evaluate_policies/run_walk_forward/_champion_challenger). See
    # _contaminated_position_ids().
    contaminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    contamination_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- Canonical monetary risk: IMMUTABLE once populated (first reconciliation only) ---
    # Set exactly once, in _sync_position_state's is_first_sight branch, via the canonical risk
    # calculator (backend/brokers/mt5/risk_calculator.py). NEVER modified afterward -- not by an
    # SL move, breakeven, trailing, TP move, partial close, backend restart, or reconciliation.
    # original_sl/original_tp/original_volume above already follow this same "set once" pattern;
    # these five columns extend it to entry price, stop distance, and money/percent/source. See
    # test_original_risk_money_immutable_after_* for the guard tests proving this.
    original_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_stop_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_risk_money: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # BROKER_NATIVE | CONSERVATIVE_MULTI_METHOD | RECONSTRUCTED | LEGACY_FALLBACK | UNAVAILABLE
    original_risk_source: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # --- Current monetary risk: MUTABLE, recomputed every management cycle ---
    # current_volume/current_sl/current_tp already exist above and are mutable. These three are
    # the money-denominated view of "if current_sl were hit right now, at current_volume, what's
    # the loss/protected-profit" -- see AdaptiveManagementService._current_risk_fields. Never
    # conflated with the original_* fields: original_risk_money answers "what did this trade
    # start out risking" (for R normalization), current_risk_money answers "what is actually at
    # stake right now" (e.g. ~0 after a breakeven move, even though original_risk_money is
    # unchanged).
    current_risk_money: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    protected_profit_money: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    remaining_open_risk_money: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    # Which basis r_now/max_achieved_r/min_achieved_r were computed on this cycle: "MONEY"
    # (preferred -- current_unrealized_pnl / original_risk_money), "PRICE_DISTANCE_FALLBACK"
    # (original_risk_money unavailable, fell back to the original-SL price-distance calc), or
    # "UNAVAILABLE" (neither reliable -- R-threshold candidates are skipped entirely that cycle;
    # see _evaluate_position). Never "current SL distance" -- see the R-anchoring incident fix.
    r_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNAVAILABLE", index=True)

    # Denormalized current stage for fast reads (position_detail, candidate-generation gating)
    # -- the durable, per-attempt record lives in AdaptivePartialProfitStageORM, keyed by the
    # same position_id. NONE | PARTIAL_1_PENDING | PARTIAL_1_EXECUTED | PARTIAL_2_PENDING |
    # PARTIAL_2_EXECUTED | RUNNER | COMPLETE | RECONCILIATION_REQUIRED.
    partial_profit_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE", index=True)

    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptiveStopQualityAuditORM(Base):
    __tablename__ = "adaptive_stop_quality_audits"

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
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
    # v2 classification using the task-specified vocabulary (VALID/TOO_TIGHT/TOO_WIDE/
    # INVALID_STRUCTURE/INVALID_BROKER_DISTANCE/UNAFFORDABLE_RISK) -- additive alongside the
    # existing `classification` column (flags-based vocabulary), which is left unchanged.
    classification_v2: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="insufficient_data", index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("position_id", name="uq_adaptive_stop_quality_position"),)


class AdaptivePartialExitStageORM(Base):
    __tablename__ = "adaptive_partial_exit_stages"

    stage_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_tp_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    requested_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    executed_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (UniqueConstraint("position_id", "stage", name="uq_adaptive_partial_stage_position_stage"),)


class AdaptivePartialProfitStageORM(Base):
    """Persistent state machine for the R-threshold-based PARTIAL_PROFIT action type (distinct
    from AdaptivePartialExitStageORM, which tracks the separate TP-progress-zone-based partial
    system). One row per stage ATTEMPT -- a retried stage after a CONFIRMED_NOT_EXECUTED result
    gets a new row, not an overwrite, so the full attempt history survives. The durable guarantee
    this table exists for: an EXECUTED stage can never execute again for the same account
    fingerprint + ticket + stage, even across cooldown expiry, backend restarts, or Docker
    restarts -- see AdaptiveManagementService._reconcile_pending_partial_stages, which resolves
    any row still PENDING at startup against the Execution Manager's own order-state record
    (ExecutionOrderORM, keyed by the same idempotency_key) rather than ever blindly resubmitting."""

    __tablename__ = "adaptive_partial_profit_stages"

    stage_attempt_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    broker_ticket: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # PARTIAL_1_PENDING | PARTIAL_1_EXECUTED | PARTIAL_2_PENDING | PARTIAL_2_EXECUTED
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    broker_deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Composite with account_id (Bug 5) -- idempotency_key alone was globally unique, relying
    # entirely on position_id's own account-prefixing convention (see _position_id's docstring)
    # to keep two accounts from ever computing the same key. Making the DB constraint itself
    # composite makes that account-scoping an enforced invariant, not an implicit side effect.
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_broker_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PENDING | CONFIRMED_EXECUTED | CONFIRMED_NOT_EXECUTED | RECONCILIATION_REQUIRED
    reconciliation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("account_id", "idempotency_key", name="uq_adaptive_partial_profit_account_idempotency"),)


class AdaptiveManagementActionORM(Base):
    __tablename__ = "adaptive_management_actions"

    action_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    activation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    thesis_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="selected", index=True)
    # Composite with account_id (Bug 5) -- see AdaptivePartialProfitStageORM.idempotency_key's
    # comment for why a single-column unique constraint here relied on an implicit invariant
    # (position_id's account-prefixing) rather than enforcing account-scoping directly.
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
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

    __table_args__ = (UniqueConstraint("account_id", "idempotency_key", name="uq_adaptive_management_action_account_idempotency"),)


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

    # breaker_id is "adaptive_demo_manager" for demo_10k (unchanged, backward compatible with
    # the existing single row) and "adaptive_demo_manager:{account_id}" for every other account
    # -- previously this was a single global row shared by ALL accounts, so one account's
    # failures could trip (or one account's manual reset could clear) every other account's
    # circuit breaker.
    breaker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="closed", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_this_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_hour_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdaptivePositionAdoptionORM(Base):
    __tablename__ = "adaptive_position_adoptions"

    adoption_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
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


class AdaptiveManagementEventORM(Base):
    """Adaptive Trade Manager Validation & Performance Analytics layer -- append-only journal
    of every per-position decision made in AdaptiveManagementService._monitor_cycle, captured
    immediately after _persist_action (see service.py) so this NEVER influences _can_execute/
    _execute_action. One row per position per cycle -- deliberately separate from
    AdaptiveManagementActionORM (the operational action journal that already exists and is
    upserted by idempotency_key) so this can be a pure, append-only observation record with a
    consistent before/after snapshot even for HOLD decisions that never touch that table's
    idempotency key differently."""

    __tablename__ = "adaptive_management_events"

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    cycle_run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    position_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_achieved_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_achieved_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The REAL action taxonomy already emitted by _select_action/_persist_action (HOLD,
    # MOVE_SL_BREAKEVEN, TRAIL_STOP, PARTIAL_PROFIT, MFE_PROTECTION_CLOSE, ... -- see
    # service.py). Never invented/renamed here.
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Coarse grouping of action_type into the minimum taxonomy this layer's spec asked for
    # (NO_ACTION/MOVE_TO_BREAK_EVEN/TRAIL_SL/MODIFY_TP/PARTIAL_EXIT/FULL_EXIT/
    # THESIS_INVALIDATED/PORTFOLIO_PROTECTION_EXIT/EMERGENCY_EXIT) -- derived, never a
    # replacement for action_type. See _categorize_action in analytics.py.
    action_category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_at_or_beyond_breakeven: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_trailing_action: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    structure_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_adaptive_mgmt_events_position_time", "position_id", "created_at"),)


class AdaptivePositionBaselineORM(Base):
    """Immutable original-trade-state baseline (Part 2). Written EXACTLY ONCE, the first time a
    position is observed by the capture hook -- never updated afterward, regardless of what the
    adaptive manager subsequently changes on AdaptivePositionStateORM.current_sl/current_tp.
    This is what lets later analytics answer "what did the manager change relative to what was
    originally placed", independent of AdaptivePositionStateORM's own (correctly mutable)
    current_sl/current_tp columns."""

    __tablename__ = "adaptive_position_baselines"

    position_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    broker_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    original_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_stop_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_reward_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_risk_money: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Joined from MT5CandidateEvaluationORM via broker_ticket, best-effort (Part 11) -- NULL for
    # positions with no matching candidate evaluation record (e.g. manually adopted positions,
    # or positions opened before the confidence-calibration layer existed).
    original_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_band: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    candidate_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AdaptiveManagerCounterfactualORM(Base):
    """Analytics-only counterfactual cache (Parts 8 & 13). One row per position, populated
    progressively and strictly after the fact by backend/adaptive_management/outcome_resolver.py
    via the SAME live/historical MT5 candle source the trading engine uses -- read-only, no
    broker order is ever placed from this table's resolution logic, and nothing here is ever
    read back into _monitor_cycle/_select_action."""

    __tablename__ = "adaptive_manager_counterfactuals"

    position_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Part 13: what would have happened if the ORIGINAL sl/tp had simply been left in place.
    # PENDING | ORIGINAL_TP_FIRST | ORIGINAL_SL_FIRST | NEITHER_WITHIN_WINDOW
    original_sltp_outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    original_sltp_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_sltp_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Part 13: no-BE baseline -- only applicable when break-even was actually activated (derived
    # from the AdaptiveManagementEventORM sl_before/sl_after series, see analytics.py).
    no_be_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    no_be_outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    no_be_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_be_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Part 8: post-exit shadow tracking, from the manager's ACTUAL exit price/time forward.
    # PENDING | RESOLVED | UNRESOLVABLE_NO_DATA (the evidence window fully elapsed but the
    # broker/bridge never returned a single candle for it -- kept distinct from RESOLVED so a
    # data gap can never be silently read as "price didn't move").
    post_exit_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    post_exit_reached_original_tp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    post_exit_reached_plus_1r: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    post_exit_reversed_strongly: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    post_exit_would_have_hit_original_sl: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Maximum favorable / adverse excursion after exit, in units of the position's original 1R
    # (initial_stop_distance) -- e.g. post_exit_mfe_r == 1.8 means price moved 1.8R further in
    # the trade's favor at its best point after the manager exited.
    post_exit_mfe_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_exit_mae_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "Potential additional R available after exit" (Part 8 spec) -- an explicit alias of
    # post_exit_mfe_r kept as its own column so callers never have to know that equivalence to
    # read the field the spec asked for by name.
    post_exit_additional_r_available: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_exit_time_to_continuation_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_exit_time_to_reversal_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Deterministic A/B/C/D-style bucket (see AdaptiveManagerOutcomeResolver._classify_post_exit
    # for the exact precedence rule and conservative same-candle tie-break):
    # PENDING | IMMEDIATE_REVERSAL (A) | MILD_CONTINUATION (B) | TP_LATER_REACHED (C) |
    # SUBSTANTIAL_R_LEFT (D) | NO_SIGNIFICANT_MOVE. Additive alongside the four booleans above,
    # which keep their original meaning unchanged.
    post_exit_classification: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    post_exit_candles_scanned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_exit_data_note: Mapped[str | None] = mapped_column(String(64), nullable=True)
    post_exit_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

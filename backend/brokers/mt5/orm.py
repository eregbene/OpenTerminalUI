from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MT5SchedulerCycleORM(Base):
    __tablename__ = "mt5_scheduler_cycles"

    cycle_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    candle_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    candle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    provider_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5_ONLY", index=True)
    symbols_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ai_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    openai_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_send_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5SchedulerCandidateORM(Base):
    __tablename__ = "mt5_scheduler_candidates"

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_class: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    ranking_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # Deterministic confidence engine (backend/brokers/mt5/confidence.py), added when the
    # OpenAI-based entry decision was removed from the autonomous cycle. Nullable: historical
    # rows persisted before this column existed have no confidence score, which is honest
    # (they were AI-decided, not confidence-scored) rather than backfilled/guessed.
    trade_confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    strategy_outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consensus: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    context_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_mt5_candidates_cycle_score", "cycle_id", "ranking_score"),)


class MT5AIDecisionORM(Base):
    __tablename__ = "mt5_ai_decisions"

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0, index=True)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5OrderRecordORM(Base):
    __tablename__ = "mt5_order_records"

    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    intent_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    retcode: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_order_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requested_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5TradeRecordORM(Base):
    __tablename__ = "mt5_trade_records"

    trade_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ai_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    lot_size: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    order_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_tickets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    # realized_pnl is NET of cost (gross_pnl + commission + swap + fee) -- see
    # backend/brokers/mt5/trading_costs.py::compute_trade_costs, the single source of truth for
    # this arithmetic across the codebase.
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)
    swap: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_trading_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission_per_lot_effective: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    open_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    close_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    strategy_outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consensus: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    broker_server: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    account_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO", index=True)
    reconciliation_state: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_mt5_trades_symbol_open", "symbol", "open_timestamp"),
        Index("ix_mt5_trades_perf", "symbol", "session", "market_regime", "exit_reason"),
    )


class MT5TradeMemorySnapshotORM(Base):
    __tablename__ = "mt5_trade_memory_snapshots"

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    closed_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    expectancy: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_win: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    average_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    mistake_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lessons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False, default="INSUFFICIENT_DATA", index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("scope", "symbol", "session", "market_regime", name="uq_mt5_memory_scope_symbol_session_regime"),
        Index("ix_mt5_memory_lookup", "symbol", "session", "market_regime", "recommendation"),
    )


class MT5CanonicalCandleORM(Base):
    __tablename__ = "mt5_canonical_candles"

    candle_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5", index=True)
    dataset_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5_ONLY", index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    tick_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    real_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality: Mapped[str] = mapped_column(String(32), nullable=False, default="VALID", index=True)
    delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    broker_server: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    # Additive columns for historical_intelligence's point-in-time integrity work (never written
    # or read by the live trading path -- backend/brokers/mt5/persistence.py::persist_candles
    # leaves these at their defaults). `timestamp` above stays exactly as the live path has
    # always written it (broker-server time for MT5, unchanged for backward compatibility);
    # `timestamp_utc` is the same instant corrected via `broker_utc_offset_minutes` as detected
    # at ingestion time -- NULL means "not yet corrected" (older rows / non-MT5-aware writers),
    # never "confirmed equal to `timestamp`". `finalized` mirrors mt5_candle_revisions.finalized
    # for this bar's CURRENT value -- see historical_intelligence/quality.py::is_finalized.
    timestamp_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_utc_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "broker_symbol", "timeframe", "timestamp", name="uq_mt5_candle_provider_symbol_tf_ts"),
        Index("ix_mt5_candles_symbol_tf_time", "canonical_symbol", "timeframe", "timestamp"),
    )


class MT5RiskMetadataMismatchORM(Base):
    """Audit record of a canonical-risk-calculator disagreement (see
    backend/brokers/mt5/risk_calculator.py) beyond the configured warning threshold
    (MT5_RISK_CALCULATION_DISAGREEMENT_PCT). One row per calculation that triggered a warning or
    critical mismatch -- never deleted/edited, so it's the durable record of every symbol whose
    broker-reported risk metadata has ever disagreed with itself (the exact class of bug behind
    the XAUUSD 10x sizing incident on ticket 57873187767)."""

    __tablename__ = "mt5_risk_metadata_mismatches"

    mismatch_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    selected_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_loss_per_lot: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimates: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    disagreement_pct: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    blocked_entry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    context: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class MT5RetentionPolicyORM(Base):
    __tablename__ = "mt5_retention_policies"

    policy_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MT5BrokerRecoveredTradeORM(Base):
    """One-time post-incident broker-history recovery target. See migration
    0037_mt5_broker_recovered_trades and backend/scripts/recover_mt5_broker_history.py.

    Deliberately separate from MT5TradeRecordORM (mt5_trade_records), whose schema
    assumes internal Bensim context (cycle_id, ai_decision_id, strategy_outputs, ...)
    that does not exist for broker-only recovered history. Every column here is a
    broker-observable fact; anything the broker doesn't retain is left NULL.
    """

    __tablename__ = "mt5_broker_recovered_trades"

    recovery_record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deal_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    deal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    volume: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    deal_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    order_setup_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_done_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profit: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    commission: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    swap: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    fee: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(256), nullable=True)
    magic: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    broker_retcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_deal_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_order_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_origin: Mapped[str] = mapped_column(String(32), nullable=False, default="MT5_BROKER_RECOVERY")
    recovered_after_incident: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recovery_incident_id: Mapped[str] = mapped_column(String(64), nullable=False, default="DB_DROP_20260807")
    recovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("account_fingerprint", "deal_ticket", name="uq_mt5_recovered_deal"),
    )


class MT5CandidateEvaluationORM(Base):
    """Confidence Validation & Calibration layer (Part 1). One immutable row per fully
    confidence-scored candidate -- selected, lower-ranked, or rejected. Deliberately separate
    from MT5SchedulerCandidateORM (which is the trading engine's own operational bookkeeping,
    upserted per cycle) so this table can serve as an append-only historical dataset that is
    never overwritten. Decision-time columns (everything up to and including
    correlated_exposure_snapshot/rule_version) are written exactly once, at insert, and never
    updated afterward. Outcome columns (outcome_type onward) start out NULL/PENDING and are
    filled in later, strictly after the fact, by the shadow tracker or the executed-trade
    linker (backend/brokers/mt5/outcome_resolver.py) -- never fed back into the live cycle.
    """

    __tablename__ = "mt5_candidate_evaluations"

    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo_10k", index=True)
    # Broker Independence Assessment Phase 1: which broker this candidate was evaluated for --
    # "MT5" | "CTRADER" (matches BROKER_PROVIDER's own values elsewhere). Every row written
    # before this column existed genuinely was MT5 (see migration 0067's own docstring for the
    # backfill rationale) -- new rows must set this explicitly once a second broker is live.
    broker_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="MT5", index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    # The exact timestamp of the last M15 bar actually used to build this candidate's
    # StrategyContext (context["timestamp"] in autonomous.py::_screen, computed live but
    # previously discarded after use) -- NOT the same instant as created_at, which is written
    # later, after prefilter/scoring/confidence-calibration/DB-write for potentially many other
    # symbols in the same cycle. historical_intelligence's replay/parity code prefers this column
    # (when present) over created_at as its point-in-time anchor, since created_at's small lag
    # behind the real market-data-fetch instant was identified as a likely source of residual
    # reconstructed-tier replay imprecision for threshold-sensitive strategies (EMA100 alignment,
    # non-session-anchored cumulative VWAP, short structural-recency windows). Nullable/additive;
    # rows written before this column existed simply fall back to created_at.
    market_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    market_regime: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # --- Multi-strategy calibration instrumentation (unified strategy layer, Part 18). All
    # nullable/default-empty so existing rows and any candidate not yet strategy-aware stay
    # valid. `strategy` above already carries the canonical strategy_id (e.g. "mtfai1",
    # "smc_continuation"); these extend it with family/fusion/conflict/evidence detail. ---
    strategy_family: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    contributing_strategies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    contributing_families: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    multi_strategy_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # NONE | RESOLVED_DOMINANT_STRENGTH | RESOLVED_HTF_DIRECTION | AMBIGUOUS_CONFLICT_REJECTED
    conflict_state: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE", index=True)
    htf_direction_h4: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    htf_direction_h1: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    raw_signal_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Standardized SMC presence flags (bos/choch/mss/displacement/liquidity_sweep/fvg/
    # order_block/premium_discount_position/session_levels_available) -- see
    # backend/mt5_strategies/context.py::summarize_smc_evidence. Attached to every candidate
    # this cycle, not only the new strategy families.
    smc_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Free-form, strategy-specific evidence (e.g. which break/sweep/FVG id contributed) --
    # distinct from the standardized smc_evidence flags above.
    strategy_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    confidence_band: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    components: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 2026-08-25 MTFAI1 V2 forward-tracking: candidate["historical_intelligence"] (status,
    # ranking_adjustment, historical_decision, peer-group evidence -- see
    # autonomous.py::_apply_historical_intelligence) was computed every cycle but never
    # persisted anywhere. Nullable/additive -- rows written before this column existed simply
    # have no HI record (honest, not backfilled/guessed).
    historical_intelligence: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    eligible_for_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    proposed_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_reward_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)

    portfolio_state_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    open_positions_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    correlated_exposure_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # DEMO-only MTFAI1 entry-quality experiment (backend.brokers.mt5.autonomous.
    # _apply_mtfai1_confirmation_gate). Set only for the one executed candidate each cycle when
    # it is MTFAI1 -- NULL/empty for every other row, including historical rows written before
    # this experiment existed (never guessed after the fact).
    mtfai1_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    mtfai1_confirming_strategy_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- Outcome tracking: NULL/PENDING at insert, filled in strictly afterward. ---
    # PENDING (not yet classified) | SHADOW (tracked via market data only, no order) |
    # EXECUTED (linked to a real MT5 position via broker_ticket).
    outcome_type: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    broker_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # PENDING | TP_HIT | SL_HIT | EXPIRED_NO_TOUCH | CLOSED (executed trades only)
    outcome_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    outcome_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    tp_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sl_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mfe_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    hypothetical_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    # realized_pnl here is NET of cost (matches mt5_trade_records.realized_pnl's meaning) --
    # gross_pnl/commission/swap/fee are the same compute_trade_costs breakdown, only populated
    # for outcome_type == EXECUTED rows (see outcome_resolver.py).
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)
    swap: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_trading_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    time_to_tp_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_sl_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_mfe_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actual_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Decision-time + tracking-window cutoff (Part 2/Part 10): a shadow candidate unresolved by
    # this timestamp is finalized as EXPIRED_NO_TOUCH rather than tracked forever.
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    outcome_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outcome_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_mt5_cand_eval_band_outcome", "confidence_band", "outcome_type", "outcome_status"),
        Index("ix_mt5_cand_eval_account_cycle_rank", "account_id", "cycle_id", "rank"),
        Index("ix_mt5_cand_eval_cycle_rank", "cycle_id", "rank"),
    )


class MT5PropDailyStateORM(Base):
    """Per-account, per-trading-day baseline for prop daily-loss/max-loss protection.
    `day_start_balance`/`day_start_equity` are captured EXACTLY ONCE, the first time an account
    is observed on a given trading_day, and never updated afterward -- this is the real daily
    baseline that challenge_status()/risk_status() must measure drawdown against, replacing the
    previous bug where every caller passed profile.expected_initial_balance (the account's
    original starting capital, never a true daily reset) as the daily baseline. `trading_day` is
    computed in the account's own PropRiskProfile.daily_reset_timezone (see prop_state.py), not
    server-local midnight or UTC, so the row naturally rolls to a new primary key at the correct
    boundary without any scheduled job. Realized P&L for the day is deliberately NOT stored here
    -- it is derived live from MT5TradeRecordORM.close_timestamp within the trading-day window,
    so it can never drift out of sync with the trade ledger or double-count on a service restart.
    """

    __tablename__ = "mt5_prop_daily_state"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trading_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    reset_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    day_start_balance: Mapped[float] = mapped_column(Float, nullable=False)
    day_start_equity: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class MT5AccountReconciliationStatusORM(Base):
    """Latest reconciliation verdict for one account (Phase 3 -- broker/internal state
    reconciliation watchdog). One row per account_id, upserted by reconciliation_watchdog.py's
    reconcile_account(). `trustworthy=False` blocks NEW entries for this account via
    is_account_state_trustworthy() -- it never touches existing-position management, which the
    Adaptive Manager continues to run directly against live broker state."""

    __tablename__ = "mt5_account_reconciliation_status"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trustworthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class MT5AccountReconciliationFindingORM(Base):
    """Append-only audit trail of every reconciliation discrepancy ever found (Phase 3) -- never
    overwritten, so "what went wrong and when" is always answerable even after the account
    returns to a trustworthy state on a later pass."""

    __tablename__ = "mt5_account_reconciliation_findings"

    finding_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    position_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (Index("ix_mrf_account_time", "account_id", "created_at"),)

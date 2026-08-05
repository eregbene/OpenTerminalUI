from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyStatus(StrEnum):
    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    PAPER_CANDIDATE = "PAPER_CANDIDATE"
    PAPER_APPROVED = "PAPER_APPROVED"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    PAUSED = "PAUSED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"
    ANALYSIS_ONLY = "ANALYSIS_ONLY"


class ExecutionMode(StrEnum):
    MANUAL_CONFIRMATION = "MANUAL_CONFIRMATION"
    RULE_BASED_AUTO_PAPER = "RULE_BASED_AUTO_PAPER"
    DISABLED = "DISABLED"


class CandidateStatus(StrEnum):
    CREATED = "CREATED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    RISK_REJECTED = "RISK_REJECTED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


class StrategyDefinition(BaseModel):
    strategy_id: str
    strategy_name: str
    strategy_version: str = "1.0.0"
    status: StrategyStatus = StrategyStatus.PAPER_CANDIDATE
    framework_dependencies: list[str] = Field(default_factory=list)
    feature_dependencies: list[str] = Field(default_factory=list)
    supported_symbols: list[str] = Field(default_factory=list)
    supported_timeframes: list[str] = Field(default_factory=list)
    supported_regimes: list[str] = Field(default_factory=list)
    unsupported_regimes: list[str] = Field(default_factory=list)
    entry_rules: list[str] = Field(default_factory=list)
    exit_rules: list[str] = Field(default_factory=list)
    stop_rules: list[str] = Field(default_factory=list)
    target_rules: list[str] = Field(default_factory=list)
    position_sizing_policy: dict[str, Any] = Field(default_factory=dict)
    maximum_holding_period: str = "2 candles"
    minimum_data_quality: float = 0.8
    validation_scorecard_id: str | None = None
    paper_deployment_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.MANUAL_CONFIRMATION
    enabled: bool = False
    supported_entry_types: list[str] = Field(default_factory=lambda: ["MARKET", "LIMIT", "STOP"])


class StrategyEligibility(BaseModel):
    strategy_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    score: float = 0


class RiskDecisionRecord(BaseModel):
    decision_id: str
    candidate_id: str
    policy_version: str = "fx-risk-v1"
    approved: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    position_size: float = 0
    estimated_risk: float = 0
    estimated_margin: float = 0
    exposure_before: dict[str, Any] = Field(default_factory=dict)
    exposure_after: dict[str, Any] = Field(default_factory=dict)
    data_used: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=utcnow)
    content_hash: str


class TradeCandidate(BaseModel):
    candidate_id: str
    idempotency_key: str
    strategy_id: str
    strategy_version: str
    paper_deployment_id: str | None = None
    symbol: str
    instrument_type: str = "FOREX"
    timeframe: str
    direction: Direction
    signal_timestamp: datetime
    candle_timestamp: datetime
    signal_basis: str = "COMPLETED_CANDLE"
    entry_type: str = "MARKET"
    candidate_entry: float
    entry_zone: dict[str, float]
    stop_price: float
    target_prices: list[float]
    risk_reward: float
    confidence: float
    framework_agreement: float
    regime: str
    session: str
    spread: float
    data_quality: float
    feature_vector_id: str | None = None
    framework_signal_ids: list[str] = Field(default_factory=list)
    source_dataset_id: str | None = None
    invalidation: str
    expiration: datetime = Field(default_factory=lambda: utcnow() + timedelta(hours=2))
    status: CandidateStatus = CandidateStatus.CREATED
    content_hash: str
    generated_at: datetime = Field(default_factory=utcnow)
    explanation: dict[str, Any] = Field(default_factory=dict)
    eligibility: list[StrategyEligibility] = Field(default_factory=list)
    risk_decision: RiskDecisionRecord | None = None
    oms_intent_id: str | None = None
    oms_order_id: str | None = None
    broker_order_id: str | None = None
    fill_ids: list[str] = Field(default_factory=list)


class ActiveTrade(BaseModel):
    trade_id: str
    candidate_id: str
    strategy_id: str
    symbol: str
    direction: Direction
    quantity: float
    entry_price: float
    current_price: float
    stop_price: float
    target_price: float
    unrealized_pnl: float
    r_multiple: float
    status: str = "OPEN"
    broker_state: str = "SIMULATED_PAPER"
    reconciliation_state: str = "MATCHED"
    opened_at: datetime = Field(default_factory=utcnow)


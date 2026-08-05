from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.market_data.models import AssetClass, DataQualityMetadata


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class StrategyFamily(StrEnum):
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    MARKET_STRUCTURE = "market_structure"
    SMC = "smart_money_concepts"
    PRICE_ACTION = "price_action"
    SESSION_BASED = "session_based"
    STATISTICAL = "statistical"
    PAIRS = "pairs"
    FACTOR_BASED = "factor_based"
    EVENT_DRIVEN = "event_driven"
    OPTIONS = "options"
    PORTFOLIO_ALLOCATION = "portfolio_allocation"
    ML_ASSISTED = "machine_learning_assisted"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    RESEARCH = "research"
    VALIDATED = "validated"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class Capability(StrEnum):
    OHLCV = "ohlcv"
    INDICATORS = "indicators"
    MARKET_STRUCTURE = "market_structure"
    MULTI_TIMEFRAME = "multi_timeframe"
    SESSION_CONTEXT = "session_context"
    DATA_QUALITY = "data_quality"


class DecisionType(StrEnum):
    LONG = "long"
    SHORT = "short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"
    HOLD = "hold"
    NO_ACTION = "no_action"
    BLOCKED = "blocked"
    INSUFFICIENT_DATA = "insufficient_data"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class StrategyMetadata(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    id: str
    name: str
    version: str = "1.0.0"
    family: StrategyFamily = StrategyFamily.MARKET_STRUCTURE
    status: StrategyStatus = StrategyStatus.RESEARCH
    description: str | None = None
    immutable: bool = False

    @field_validator("version")
    @classmethod
    def _semantic_version(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError("strategy version must use MAJOR.MINOR.PATCH")
        return value


class StrategyUniverse(BaseModel):
    asset_classes: list[AssetClass] = Field(default_factory=lambda: [AssetClass.EQUITY, AssetClass.FOREX, AssetClass.CRYPTO])
    symbols: list[str] = Field(default_factory=list)


class StrategyTimeframes(BaseModel):
    execution: str = "15m"
    context: list[str] = Field(default_factory=list)


class StrategySchedule(BaseModel):
    sessions: list[str] = Field(default_factory=list)
    weekdays: list[str] = Field(default_factory=lambda: ["monday", "tuesday", "wednesday", "thursday", "friday"])


class DataPolicy(BaseModel):
    allow_delayed: bool = True
    allow_cached: bool = True
    allow_fallback: bool = False
    allow_simulated: bool = False
    minimum_quality_score: float | None = Field(default=None, ge=0, le=1)
    maximum_age_seconds: int | None = Field(default=None, ge=0)


class Condition(BaseModel):
    id: str | None = None
    feature: str
    operator: str
    value: Any = None
    lookback: int | None = Field(default=None, ge=0, le=500)
    timeframe: str | None = None


class RuleGroup(BaseModel):
    id: str | None = None
    all: list["RuleNode"] | None = None
    any: list["RuleNode"] | None = None
    not_: "RuleNode | None" = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _one_operator(self) -> "RuleGroup":
        used = [self.all is not None, self.any is not None, self.not_ is not None]
        if sum(used) != 1:
            raise ValueError("rule group must specify exactly one of all, any, not")
        return self


RuleNode = Condition | RuleGroup
RuleGroup.model_rebuild()


class EntryRules(BaseModel):
    long: RuleNode | None = None
    short: RuleNode | None = None


class ExitRules(BaseModel):
    long: RuleNode | None = None
    short: RuleNode | None = None


class InvalidationRule(BaseModel):
    type: str = "atr_distance"
    reference: str = "price.close"
    value: float | None = None
    buffer_atr: float = 0.0


class TargetRule(BaseModel):
    type: str = "risk_reward"
    value: float = 2.0
    reference: str | None = None


class SizingIntent(BaseModel):
    type: str = "percentage_risk"
    value: float = 1.0
    risk_budget_percent: float | None = None
    entry_reference: str | None = None
    invalidation_reference: str | None = None


class CooldownRule(BaseModel):
    bars: int = Field(default=0, ge=0, le=10000)


class StrategySpec(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)
    strategy: StrategyMetadata
    universe: StrategyUniverse = Field(default_factory=StrategyUniverse)
    timeframes: StrategyTimeframes = Field(default_factory=StrategyTimeframes)
    schedule: StrategySchedule = Field(default_factory=StrategySchedule)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)
    entry: EntryRules
    exit: ExitRules = Field(default_factory=ExitRules)
    invalidation: InvalidationRule = Field(default_factory=InvalidationRule)
    targets: list[TargetRule] = Field(default_factory=lambda: [TargetRule()])
    sizing_intent: SizingIntent = Field(default_factory=SizingIntent)
    cooldown: CooldownRule = Field(default_factory=CooldownRule)
    required_features: list[str] = Field(default_factory=list)
    required_history: int = Field(default=30, ge=1, le=10000)

    def stable_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=False)

    def strategy_hash(self) -> str:
        raw = json.dumps(self.stable_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class StrategyRegistration(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    strategy_id: str
    name: str
    family: StrategyFamily
    version: str
    status: StrategyStatus
    supported_asset_classes: list[AssetClass]
    supported_timeframes: list[str]
    required_capabilities: list[Capability]
    required_features: list[str]
    required_history: int
    allows_long: bool = True
    allows_short: bool = True
    supports_batch: bool = True
    supports_incremental: bool = True
    supports_multi_timeframe: bool = False
    supports_portfolio_context: bool = False
    experimental: bool = True


class ConditionResult(BaseModel):
    condition_id: str
    feature: str
    operator: str
    expected_value: Any = None
    observed_value: Any = None
    result: bool
    evaluated_at: datetime
    source_timeframe: str | None = None
    source_timestamp: datetime | None = None
    reason: str

    @field_validator("evaluated_at", "source_timestamp")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None


class GroupResult(BaseModel):
    group_id: str
    operator: Literal["all", "any", "not"]
    result: bool
    child_results: list["EvaluationResult"]


EvaluationResult = ConditionResult | GroupResult
GroupResult.model_rebuild()


class StrategyContext(BaseModel):
    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)
    instrument_id: str
    symbol: str
    asset_class: AssetClass = AssetClass.UNKNOWN
    venue: str | None = None
    execution_timeframe: str
    context_timeframes: list[str] = Field(default_factory=list)
    as_of_timestamp: datetime
    completed_bars: list[dict[str, Any]]
    current_bar: dict[str, Any]
    indicator_values: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    feature_timestamps: dict[str, datetime] = Field(default_factory=dict)
    market_structure_snapshot: dict[str, Any] | None = None
    session_context: dict[str, Any] = Field(default_factory=dict)
    data_quality: DataQualityMetadata | None = None
    dataset_snapshot_id: str | None = None
    strategy_version: str
    strategy_hash: str
    portfolio_context_reference: str | None = None
    account_context_reference: str | None = None

    @field_validator("as_of_timestamp")
    @classmethod
    def _as_of_utc(cls, value: datetime) -> datetime:
        return utc(value)


class TargetLevel(BaseModel):
    type: str
    price: float
    reference: str | None = None
    rationale: str


class TradeProposal(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    proposal_id: str
    strategy_decision_id: str
    instrument_id: str
    asset_class: AssetClass
    direction: Direction
    proposal_time: datetime
    reference_price: float
    entry_intent: str
    entry_price_reference: str
    invalidation_price: float | None = None
    target_levels: list[TargetLevel] = Field(default_factory=list)
    time_in_force_intent: str = "next_valid_event"
    sizing_intent: SizingIntent = Field(default_factory=SizingIntent)
    maximum_holding_period: int | None = None
    expiry_time: datetime | None = None
    rationale: str
    quality_score: float = Field(default=0, ge=0, le=1)
    data_quality: DataQualityMetadata | None = None
    status: ProposalStatus = ProposalStatus.PROPOSED


class StrategyDecision(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    decision_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    symbol: str
    timeframe: str
    as_of_timestamp: datetime
    direction: Direction = Direction.NEUTRAL
    decision_type: DecisionType
    rule_result: EvaluationResult | None = None
    confidence_label: str = "deterministic"
    quality_score: float = Field(default=0, ge=0, le=1)
    explanation: str
    input_references: dict[str, Any] = Field(default_factory=dict)
    dataset_snapshot_id: str | None = None
    configuration_hash: str
    data_quality: DataQualityMetadata | None = None
    proposal: TradeProposal | None = None


class StrategyState(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    strategy_id: str
    strategy_hash: str
    instrument_id: str
    state: str = "idle"
    last_decision_time: datetime | None = None
    last_proposal_bar_index: int | None = None
    active_proposal_ids: list[str] = Field(default_factory=list)
    consumed_event_ids: list[str] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)


class StrategyEvent(BaseModel):
    event_type: str
    schema_version: str = "1.0"
    event_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    as_of_timestamp: datetime
    dataset_snapshot_id: str | None = None
    correlation_id: str
    idempotency_key: str
    evidence_reference: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StrategyEvaluation(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    evaluation_id: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    symbol: str
    timeframe: str
    dataset_snapshot_id: str | None = None
    decisions: list[StrategyDecision] = Field(default_factory=list)
    proposals: list[TradeProposal] = Field(default_factory=list)
    state: StrategyState
    events: list[StrategyEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    feature_rows: list[dict[str, Any]] = Field(default_factory=list)


def stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

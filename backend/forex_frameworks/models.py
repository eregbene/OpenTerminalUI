from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FrameworkStatus(StrEnum):
    IMPLEMENTED = "IMPLEMENTED"
    LIMITED = "LIMITED"
    RESEARCH = "RESEARCH"
    PLACEHOLDER = "PLACEHOLDER"


class SignalStatus(StrEnum):
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNSUPPORTED_REGIME = "UNSUPPORTED_REGIME"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    STALE_DATA = "STALE_DATA"
    INVALID = "INVALID"


class FrameworkBias(StrEnum):
    STRONGLY_BULLISH = "STRONGLY_BULLISH"
    BULLISH = "BULLISH"
    SLIGHTLY_BULLISH = "SLIGHTLY_BULLISH"
    NEUTRAL = "NEUTRAL"
    SLIGHTLY_BEARISH = "SLIGHTLY_BEARISH"
    BEARISH = "BEARISH"
    STRONGLY_BEARISH = "STRONGLY_BEARISH"
    UNKNOWN = "UNKNOWN"


class PriceZone(BaseModel):
    label: str
    low: float
    high: float
    source: str


class FrameworkContext(BaseModel):
    symbol: str
    asset_type: str
    instrument_class: str
    timeframe: str
    analysis_timestamp: datetime
    completed_candles: list[dict[str, Any]] = Field(default_factory=list)
    feature_vectors: list[dict[str, Any]] = Field(default_factory=list)
    current_feature: dict[str, Any] = Field(default_factory=dict)
    market_structure: dict[str, Any] = Field(default_factory=dict)
    indicators: dict[str, Any] = Field(default_factory=dict)
    liquidity: dict[str, Any] = Field(default_factory=dict)
    session: dict[str, Any] = Field(default_factory=dict)
    volatility: dict[str, Any] = Field(default_factory=dict)
    trend: dict[str, Any] = Field(default_factory=dict)
    momentum: dict[str, Any] = Field(default_factory=dict)
    regime: dict[str, Any] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    data_quality_status: str = "ok"
    feature_version: str
    feature_vector_id: str | None = None
    source_dataset_id: str | None = None


class FrameworkSignal(BaseModel):
    framework_id: str
    framework_name: str
    framework_version: str
    symbol: str
    timeframe: str
    analysis_timestamp: datetime
    bias: FrameworkBias
    signal_type: str
    confidence: float = Field(ge=0, le=1)
    quality: float = Field(ge=0, le=1)
    entry_zone: PriceZone | None = None
    invalidation_zone: PriceZone | None = None
    target_zones: list[PriceZone] = Field(default_factory=list)
    risk_reward_estimate: float | None = None
    market_regime: str = "unknown"
    supporting_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: SignalStatus
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class FrameworkDefinition(BaseModel):
    framework_id: str
    display_name: str
    version: str
    status: FrameworkStatus
    enabled: bool = True
    group: str
    supported_symbols: list[str]
    supported_timeframes: list[str]
    limitations: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0)


class FrameworkComparison(BaseModel):
    symbol: str
    timeframe: str
    analysis_timestamp: datetime
    bullish_framework_count: int
    bearish_framework_count: int
    neutral_framework_count: int
    unknown_framework_count: int
    weighted_bullish_score: float
    weighted_bearish_score: float
    agreement_ratio: float
    conflict_ratio: float
    data_quality_score: float
    overall_framework_bias: FrameworkBias
    overlapping_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    signals: list[FrameworkSignal] = Field(default_factory=list)


class FrameworkThesis(BaseModel):
    symbol: str
    timeframe: str
    directional_bias: FrameworkBias
    confidence: float = Field(ge=0, le=1)
    market_regime: str
    framework_agreement: dict[str, Any]
    framework_disagreement: list[str]
    candidate_entry_zone: PriceZone | None = None
    invalidation: PriceZone | None = None
    candidate_target_zones: list[PriceZone] = Field(default_factory=list)
    risk_reward_estimate: float | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    data_freshness: str = "unknown"
    framework_versions: dict[str, str] = Field(default_factory=dict)
    read_only: bool = True
    label: str = "analytical_candidate"

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.market_data.models import AssetClass, DataQualityMetadata


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ConceptStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    PARTIAL = "partial"
    MITIGATED = "mitigated"
    INVALIDATED = "invalidated"
    SWEPT = "swept"


class ConceptType(StrEnum):
    SWING_POINT = "swing_point"
    STRUCTURE_LEG = "structure_leg"
    STRUCTURE_RANGE = "structure_range"
    TREND_STATE = "trend_state"
    STRUCTURE_BREAK = "structure_break"
    DISPLACEMENT = "displacement"
    LIQUIDITY_LEVEL = "liquidity_level"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    IMBALANCE_ZONE = "imbalance_zone"
    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"
    DEALING_RANGE = "dealing_range"
    PREMIUM_DISCOUNT_ZONE = "premium_discount_zone"
    SESSION_LEVEL = "session_level"
    OTE_ZONE = "ote_zone"


class TrendLabel(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"
    TRANSITIONAL = "transitional"
    UNKNOWN = "unknown"


class StructureBreakKind(StrEnum):
    BOS = "bos"
    CHOCH = "choch"
    MSS = "mss"
    UNCLASSIFIED = "unclassified_break"


class LiquiditySide(StrEnum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class StructureScope(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNSPECIFIED = "unspecified"


class BaseStructureObject(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    concept_type: ConceptType | str
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    timeframe: str
    start_time: datetime
    end_time: datetime
    detected_time: datetime
    confirmation_time: datetime | None = None
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    direction: Direction = Direction.UNKNOWN
    status: ConceptStatus = ConceptStatus.CONFIRMED
    strength: float | None = Field(default=None, ge=0)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    configuration_version: str
    configuration_hash: str
    source_dataset_id: str | None = None
    supporting_bar_indexes: list[int] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    parent_structure_id: str | None = None
    invalidation_time: datetime | None = None
    mitigation_time: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("start_time", "end_time", "detected_time", "confirmation_time", "invalidation_time", "mitigation_time")
    @classmethod
    def _timestamps_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None


class SwingPoint(BaseStructureObject):
    concept_type: ConceptType = ConceptType.SWING_POINT
    swing_type: str
    bar_index: int
    price: Decimal
    candidate_time: datetime


class SwingSequence(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    swings: list[SwingPoint] = Field(default_factory=list)


class StructureLeg(BaseStructureObject):
    concept_type: ConceptType = ConceptType.STRUCTURE_LEG
    start_swing_id: str
    end_swing_id: str
    magnitude: Decimal
    magnitude_atr: float | None = None
    scope: StructureScope = StructureScope.UNSPECIFIED


class StructureRange(BaseStructureObject):
    concept_type: ConceptType = ConceptType.STRUCTURE_RANGE
    high_swing_id: str
    low_swing_id: str
    midpoint: Decimal
    scope: StructureScope = StructureScope.UNSPECIFIED


class TrendState(BaseStructureObject):
    concept_type: ConceptType = ConceptType.TREND_STATE
    state: TrendLabel
    evidence: list[str] = Field(default_factory=list)


class StructureBreak(BaseStructureObject):
    concept_type: ConceptType = ConceptType.STRUCTURE_BREAK
    break_kind: StructureBreakKind
    broken_swing_id: str
    broken_level: Decimal
    break_price: Decimal
    break_distance: Decimal
    break_distance_atr: float | None = None
    confirmation_mode: str
    continuation_direction: Direction = Direction.UNKNOWN
    bar_index: int
    explanation: str


class DisplacementEvent(BaseStructureObject):
    concept_type: ConceptType = ConceptType.DISPLACEMENT
    bar_index: int
    magnitude_atr: float | None = None
    body_ratio: float
    bars_in_sequence: int
    volume_ratio: float | None = None
    created_imbalance: bool = False


class LiquidityLevel(BaseStructureObject):
    concept_type: ConceptType = ConceptType.LIQUIDITY_LEVEL
    side: LiquiditySide
    level: Decimal
    touch_count: int = 1
    source: str
    tolerance: Decimal = Decimal("0")


class LiquiditySweep(BaseStructureObject):
    concept_type: ConceptType = ConceptType.LIQUIDITY_SWEEP
    level_id: str
    side: LiquiditySide
    swept_price: Decimal
    reclaim_price: Decimal | None = None
    penetration: Decimal
    bar_index: int


class ImbalanceZone(BaseStructureObject):
    concept_type: ConceptType = ConceptType.IMBALANCE_ZONE
    zone_type: str = "fair_value_gap"
    midpoint: Decimal
    first_mitigation_time: datetime | None = None
    consequent_encroachment: Decimal


class OrderBlock(BaseStructureObject):
    concept_type: ConceptType = ConceptType.ORDER_BLOCK
    source_break_id: str | None = None
    origin_bar_index: int
    rule: str


class BreakerBlock(OrderBlock):
    concept_type: ConceptType = ConceptType.BREAKER_BLOCK


class MitigationBlock(OrderBlock):
    concept_type: ConceptType = ConceptType.MITIGATION_BLOCK


class DealingRange(BaseStructureObject):
    concept_type: ConceptType = ConceptType.DEALING_RANGE
    high_swing_id: str
    low_swing_id: str
    equilibrium: Decimal
    normalized_current_position: float | None = Field(default=None, ge=0, le=1)


class PremiumDiscountZone(BaseStructureObject):
    concept_type: ConceptType = ConceptType.PREMIUM_DISCOUNT_ZONE
    range_id: str
    zone_name: str
    lower_bound: Decimal
    upper_bound: Decimal


class SessionLevel(BaseStructureObject):
    concept_type: ConceptType = ConceptType.SESSION_LEVEL
    session_name: str
    level_name: str
    level: Decimal


class OverlayObject(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    overlay_id: str
    type: str
    timestamp: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    price: Decimal | None = None
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    direction: Direction = Direction.UNKNOWN
    label: str
    style_role: str
    source_event_id: str | None = None
    status: ConceptStatus = ConceptStatus.ACTIVE
    tooltip_evidence: list[str] = Field(default_factory=list)


class MarketStructureEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    event_type: str
    schema_version: str = "1.0"
    event_id: str
    correlation_id: str
    idempotency_key: str
    occurred_at: datetime
    source_dataset_id: str | None = None
    configuration_hash: str
    payload: dict[str, Any]


class ScoreComponent(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float
    evidence: str


class QualityScore(BaseModel):
    total_score: float = Field(ge=0, le=1)
    score_components: list[ScoreComponent] = Field(default_factory=list)


class FeatureRow(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    timestamp: datetime
    trend_state: TrendLabel
    last_bos_direction: Direction = Direction.UNKNOWN
    bars_since_bos: int | None = None
    choch_active: bool = False
    displacement_score: float = 0.0
    nearest_buy_side_liquidity_distance_atr: float | None = None
    nearest_sell_side_liquidity_distance_atr: float | None = None
    inside_bullish_fvg: bool = False
    inside_bearish_order_block: bool = False
    dealing_range_position: float | None = None
    higher_timeframe_alignment: str = "neutral"
    session_name: str | None = None


class EngineState(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    recent_bars: list[dict[str, Any]] = Field(default_factory=list)
    pending_swing_candidates: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_swings: list[SwingPoint] = Field(default_factory=list)
    active_ranges: list[DealingRange] = Field(default_factory=list)
    active_liquidity_levels: list[LiquidityLevel] = Field(default_factory=list)
    active_gaps: list[ImbalanceZone] = Field(default_factory=list)
    active_blocks: list[OrderBlock] = Field(default_factory=list)
    last_processed_timestamp: datetime | None = None
    configuration_hash: str
    source_dataset_id: str | None = None


class MarketStructureSnapshot(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    snapshot_id: str
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    timeframe: str
    analysis_timestamp: datetime
    configuration_version: str
    configuration_hash: str
    configuration: dict[str, Any]
    source_dataset_id: str | None = None
    data_quality: DataQualityMetadata | None = None
    swings: list[SwingPoint] = Field(default_factory=list)
    legs: list[StructureLeg] = Field(default_factory=list)
    ranges: list[StructureRange] = Field(default_factory=list)
    trend: TrendState | None = None
    breaks: list[StructureBreak] = Field(default_factory=list)
    displacements: list[DisplacementEvent] = Field(default_factory=list)
    liquidity_levels: list[LiquidityLevel] = Field(default_factory=list)
    liquidity_sweeps: list[LiquiditySweep] = Field(default_factory=list)
    imbalances: list[ImbalanceZone] = Field(default_factory=list)
    order_blocks: list[OrderBlock] = Field(default_factory=list)
    dealing_ranges: list[DealingRange] = Field(default_factory=list)
    premium_discount_zones: list[PremiumDiscountZone] = Field(default_factory=list)
    session_levels: list[SessionLevel] = Field(default_factory=list)
    overlays: list[OverlayObject] = Field(default_factory=list)
    events: list[MarketStructureEvent] = Field(default_factory=list)
    features: list[FeatureRow] = Field(default_factory=list)
    score: QualityScore | None = None
    warnings: list[str] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
    state: EngineState


def stable_id(prefix: str, *parts: object) -> str:
    raw = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

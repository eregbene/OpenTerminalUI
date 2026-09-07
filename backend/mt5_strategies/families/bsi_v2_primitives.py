"""BSI V2 mentor-specific primitives.

These objects sit above generic market_structure output and below future V2
strategy evaluators. They do not mutate or reinterpret shared raw primitives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from backend.market_structure.models import stable_id
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL


class BSIEvidenceClass(StrEnum):
    SPOKEN_EXPLICIT = "SPOKEN_EXPLICIT"
    VISUAL_EXPLICIT = "VISUAL_EXPLICIT"
    SPOKEN_AND_VISUAL = "SPOKEN_AND_VISUAL"
    VISUAL_INFERENCE = "VISUAL_INFERENCE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    AMBIGUOUS = "AMBIGUOUS"
    BENSIM_ENGINEERING = "BENSIM_ENGINEERING"


class BSIMentorStructureKind(StrEnum):
    MSB = "msb"
    MSS = "mss"


class BSILiquidityType(StrEnum):
    SWING_LEVEL = "swing_level"
    EQUAL_LEVEL = "equal_level"
    SESSION_BOX_EDGE = "session_box_edge"
    TRENDLINE_LIQUIDITY = "trendline_liquidity"
    ORIGIN_OB_EDGE = "origin_ob_edge"
    RESIDUAL_WICK_LIQUIDITY = "residual_wick_liquidity"


class BSILiquidityStatus(StrEnum):
    ACTIVE = "active"
    TAKEN = "taken"
    INVALIDATED = "invalidated"


class BSIPremiumDiscount(StrEnum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    AT_EQUILIBRIUM = "at_equilibrium"


class BSILifecycleState(StrEnum):
    DETECTED = "DETECTED"
    THESIS_CREATED = "THESIS_CREATED"
    ENTRY_ARMED = "ENTRY_ARMED"
    ENTRY_AVAILABLE = "ENTRY_AVAILABLE"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class BSIRuleEvidence:
    rule_id: str
    evidence_class: BSIEvidenceClass | str
    source_document: str


@dataclass(frozen=True)
class BSIMentorStructureEvent:
    source_break_id: str
    direction_before: str
    direction_after: str
    mentor_classification: BSIMentorStructureKind
    relevant_swing_id: str | None
    break_level: Decimal
    break_price: Decimal
    break_bar_index: int
    break_time: datetime
    source_rule_ids: tuple[str, ...]
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL


@dataclass(frozen=True)
class BSIMentorFVGRelation:
    fvg_id: str
    candle_1_index: int
    impulse_candle_index: int
    candle_3_index: int
    low: Decimal
    high: Decimal
    direction: str
    mitigation_state: str
    source_rule_ids: tuple[str, ...]
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL


@dataclass(frozen=True)
class BSIMentorOrderBlock:
    mentor_ob_id: str
    fvg_id: str
    fvg_relation: BSIMentorFVGRelation
    selected_ob_candle_index: int
    low: Decimal
    high: Decimal
    direction: str
    dealing_zone_relationship: str | None
    reason_selected: str
    rejected_competing_ob_ids: tuple[str, ...] = ()
    source_rule_ids: tuple[str, ...] = ("BSI2-OB-001", "BSI2-FVG-001")
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL


@dataclass(frozen=True)
class BSITrendlineGeometry:
    anchor_1_id: str
    anchor_2_id: str
    anchor_1_bar_index: int
    anchor_2_bar_index: int
    anchor_1_price: Decimal
    anchor_2_price: Decimal
    slope: Decimal
    intercept: Decimal
    active_from_bar_index: int
    active_until_bar_index: int | None = None

    def projected_value(self, bar_index: int) -> Decimal:
        return self.slope * Decimal(bar_index) + self.intercept


@dataclass(frozen=True)
class BSILiquidityObject:
    liquidity_id: str
    liquidity_type: BSILiquidityType
    side: str
    source_timeframe: str
    creation_time: datetime
    source_swing_ids: tuple[str, ...] = ()
    geometry: dict[str, Any] = field(default_factory=dict)
    reference_level: Decimal | None = None
    status: BSILiquidityStatus = BSILiquidityStatus.ACTIVE
    taken: bool = False
    taken_time: datetime | None = None
    source_rule_ids: tuple[str, ...] = ()
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL


@dataclass(frozen=True)
class BSILiquidityTakenEvent:
    liquidity_id: str
    liquidity_type: BSILiquidityType
    original_liquidity_level: Decimal
    sweep_extreme: Decimal
    event_bar_index: int
    event_time: datetime
    side: str
    source_rule_ids: tuple[str, ...]
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL


@dataclass(frozen=True)
class BSIDealingLeg:
    leg_id: str
    source_break_id: str
    start_anchor_id: str
    end_anchor_id: str
    start_price: Decimal
    end_price: Decimal
    start_bar_index: int
    end_bar_index: int
    source_rule_ids: tuple[str, ...]
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL

    @property
    def low(self) -> Decimal:
        return min(self.start_price, self.end_price)

    @property
    def high(self) -> Decimal:
        return max(self.start_price, self.end_price)

    @property
    def midpoint(self) -> Decimal:
        return (self.start_price + self.end_price) / Decimal("2")

    def classify(self, price: Decimal) -> BSIPremiumDiscount:
        if price == self.midpoint:
            return BSIPremiumDiscount.AT_EQUILIBRIUM
        return BSIPremiumDiscount.PREMIUM if price > self.midpoint else BSIPremiumDiscount.DISCOUNT


@dataclass(frozen=True)
class BSIEntryArray:
    array_id: str
    array_type: str
    low: Decimal
    high: Decimal
    source_fvg_id: str | None = None
    source_ob_id: str | None = None


@dataclass(frozen=True)
class BSIIdentitySeed:
    subtype: str
    symbol: str
    direction: str
    timeframe: str
    structure_event_id: str | None = None
    liquidity_id: str | None = None
    entry_array_id: str | None = None
    retest_level: Decimal | None = None
    version: str = BSI_BASELINE_V2_AUDIOVISUAL


def build_bsi_thesis_id(seed: BSIIdentitySeed) -> str:
    return stable_id(
        "bsi2_thesis",
        seed.version,
        seed.subtype,
        seed.symbol,
        seed.direction,
        seed.timeframe,
        seed.structure_event_id,
        seed.liquidity_id,
    )


def build_bsi_entry_opportunity_id(seed: BSIIdentitySeed) -> str:
    return stable_id(
        "bsi2_entry",
        build_bsi_thesis_id(seed),
        seed.entry_array_id,
        seed.retest_level,
    )


@dataclass(frozen=True)
class BSIV2Evidence:
    bsi_version: str
    strategy_subtype: str
    mentor_rule_ids: tuple[str, ...] = ()
    structure: BSIMentorStructureEvent | None = None
    relevant_swing_ids: tuple[str, ...] = ()
    liquidity: BSILiquidityObject | None = None
    liquidity_taken: BSILiquidityTakenEvent | None = None
    dealing_leg: BSIDealingLeg | None = None
    premium_discount: BSIPremiumDiscount | None = None
    fvg: BSIMentorFVGRelation | None = None
    mentor_ob: BSIMentorOrderBlock | None = None
    entry_array: BSIEntryArray | None = None
    bsi_thesis_id: str | None = None
    bsi_entry_opportunity_id: str | None = None
    mentor_invalidation: dict[str, Any] = field(default_factory=dict)
    target_semantics: dict[str, Any] = field(default_factory=dict)
    engineering_adjustments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bsi_version": self.bsi_version,
            "strategy_subtype": self.strategy_subtype,
            "mentor_rule_ids": list(self.mentor_rule_ids),
            "bsi_thesis_id": self.bsi_thesis_id,
            "bsi_entry_opportunity_id": self.bsi_entry_opportunity_id,
            "premium_discount": self.premium_discount.value if self.premium_discount else None,
            "mentor_invalidation": self.mentor_invalidation,
            "target_semantics": self.target_semantics,
            "engineering_adjustments": self.engineering_adjustments,
        }


@dataclass(frozen=True)
class BSILifecycleTransition:
    bsi_thesis_id: str
    bsi_entry_opportunity_id: str
    from_state: BSILifecycleState | None
    to_state: BSILifecycleState
    reason: str
    occurred_at: datetime
    source_rule_ids: tuple[str, ...] = ("BSI2-LIFE-001",)
    evidence_class: BSIEvidenceClass = BSIEvidenceClass.BENSIM_ENGINEERING


@dataclass(frozen=True)
class BSIFreshnessDecision:
    status: str
    reason: str
    entry_price: Decimal | None
    target: Decimal | None
    reward_risk: Decimal | None
    is_executable: bool
    mentor_entry_valid: bool
    mentor_setup_valid: bool
    execution_viable: bool
    engineering_adjustments: dict[str, Any] = field(default_factory=dict)

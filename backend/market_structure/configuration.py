from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SwingMethod(StrEnum):
    FRACTAL = "fractal"


class BreakConfirmation(StrEnum):
    WICK = "wick"
    CLOSE = "close"
    BODY_CLOSE = "body_close"
    CLOSE_PLUS_DISTANCE = "close_plus_distance"
    CLOSE_PLUS_DISPLACEMENT = "close_plus_displacement"


class MarketStructureProfile(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    BALANCED = "balanced"


class InputConfig(BaseModel):
    use_completed_bars_only: bool = True
    allow_incomplete_last_bar: bool = False


class SwingConfig(BaseModel):
    method: SwingMethod = SwingMethod.FRACTAL
    left_bars: int = Field(default=3, ge=1, le=20)
    right_bars: int = Field(default=3, ge=1, le=20)
    minimum_separation_bars: int = Field(default=2, ge=0, le=100)
    minimum_price_movement: float = Field(default=0.0, ge=0)
    minimum_atr: float = Field(default=0.0, ge=0)


class StructureConfig(BaseModel):
    break_confirmation: BreakConfirmation = BreakConfirmation.CLOSE
    minimum_break_atr: float = Field(default=0.10, ge=0)
    require_displacement: bool = False
    choch_requires_prior_trend: bool = True
    mss_requires_displacement: bool = True


class DisplacementConfig(BaseModel):
    atr_period: int = Field(default=14, ge=2, le=200)
    body_atr: float = Field(default=1.0, ge=0)
    range_atr: float = Field(default=1.2, ge=0)
    minimum_body_ratio: float = Field(default=0.55, ge=0, le=1)
    consecutive_bars: int = Field(default=1, ge=1, le=10)
    volume_ratio: float | None = Field(default=None, ge=0)


class EqualLevelConfig(BaseModel):
    tolerance_atr: float = Field(default=0.10, ge=0)
    tolerance_absolute: float | None = Field(default=None, ge=0)
    tolerance_percent: float | None = Field(default=None, ge=0)
    minimum_touches: int = Field(default=2, ge=2, le=10)


class FvgConfig(BaseModel):
    enabled: bool = True
    minimum_size_atr: float = Field(default=0.05, ge=0)
    use_wicks: bool = True
    mitigation_mode: str = "touch"


class OrderBlockConfig(BaseModel):
    enabled: bool = True
    method: str = "last_opposing_candle_before_displacement_and_break"
    require_displacement: bool = True
    require_structure_break: bool = True
    zone_source: str = "full_range"
    max_search_bars: int = Field(default=10, ge=1, le=100)
    invalidation: str = "close_through_zone"
    mitigation: str = "touch"


class DealingRangeConfig(BaseModel):
    enabled: bool = True
    subdivisions: tuple[float, ...] = (0.0, 0.5, 1.0)
    ote_lower_retracement: float = Field(default=0.62, ge=0, le=1)
    ote_upper_retracement: float = Field(default=0.79, ge=0, le=1)


class SessionWindow(BaseModel):
    name: str
    timezone: str = "UTC"
    start: str
    end: str
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    asset_classes: tuple[str, ...] = ("forex", "future", "equity", "crypto")


class SessionConfig(BaseModel):
    timezone: str = "UTC"
    windows: tuple[SessionWindow, ...] = (
        SessionWindow(name="asian", start="00:00", end="06:00"),
        SessionWindow(name="london", start="07:00", end="10:00"),
        SessionWindow(name="new_york_morning", start="13:30", end="16:00"),
        SessionWindow(name="new_york_afternoon", start="18:00", end="20:00"),
    )


class MultiTimeframeConfig(BaseModel):
    enabled: bool = True
    hierarchy: tuple[str, ...] = ("1d", "4h", "15m", "5m")


class ScoringConfig(BaseModel):
    break_distance_weight: float = 0.25
    displacement_weight: float = 0.25
    liquidity_weight: float = 0.20
    imbalance_weight: float = 0.15
    range_location_weight: float = 0.15


class MarketStructureConfig(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    engine_name: str = "bensim-smc"
    version: str = "1.0"
    profile: MarketStructureProfile = MarketStructureProfile.BALANCED
    input: InputConfig = Field(default_factory=InputConfig)
    swings: SwingConfig = Field(default_factory=SwingConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    displacement: DisplacementConfig = Field(default_factory=DisplacementConfig)
    equal_levels: EqualLevelConfig = Field(default_factory=EqualLevelConfig)
    fvg: FvgConfig = Field(default_factory=FvgConfig)
    order_blocks: OrderBlockConfig = Field(default_factory=OrderBlockConfig)
    dealing_range: DealingRangeConfig = Field(default_factory=DealingRangeConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    multi_timeframe: MultiTimeframeConfig = Field(default_factory=MultiTimeframeConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    @field_validator("version")
    @classmethod
    def _version_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version is required")
        return value.strip()

    def stable_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)

    def configuration_hash(self) -> str:
        raw = json.dumps(self.stable_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_profile(name: str | MarketStructureProfile = MarketStructureProfile.BALANCED) -> MarketStructureConfig:
    profile = MarketStructureProfile(str(name).lower())
    if profile == MarketStructureProfile.INTERNAL:
        return MarketStructureConfig(
            profile=profile,
            swings=SwingConfig(left_bars=2, right_bars=2, minimum_separation_bars=1, minimum_atr=0.05),
            structure=StructureConfig(minimum_break_atr=0.05),
        )
    if profile == MarketStructureProfile.EXTERNAL:
        return MarketStructureConfig(
            profile=profile,
            swings=SwingConfig(left_bars=5, right_bars=5, minimum_separation_bars=3, minimum_atr=0.20),
            structure=StructureConfig(minimum_break_atr=0.15, require_displacement=True),
        )
    return MarketStructureConfig(profile=profile)

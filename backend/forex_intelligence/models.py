from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ForexIndicatorSnapshot(BaseModel):
    atr: float | None = None
    historical_volatility: float | None = None
    adr: float | None = None
    expected_daily_range: float | None = None
    volatility_state: str = "unknown"
    ema_fast: float | None = None
    ema_slow: float | None = None
    adx: float | None = None
    regression_slope: float | None = None
    trend_score: float = 0.0
    rsi: float | None = None
    stochastic_rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    roc: float | None = None
    momentum_score: float = 0.0


class ForexFeatureVector(BaseModel):
    timestamp: datetime
    symbol: str
    timeframe: str
    close: float
    session: str
    session_range: float | None = None
    structure_trend: str = "unknown"
    latest_break: str | None = None
    latest_break_direction: str | None = None
    liquidity_sweeps: int = 0
    active_fvgs: int = 0
    active_order_blocks: int = 0
    premium_discount: str = "unknown"
    dealing_range_position: float | None = None
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volatility_state: str = "unknown"
    confluence_score: float = Field(default=0.0, ge=0, le=1)
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class ForexIntelligenceSnapshot(BaseModel):
    snapshot_id: str
    symbol: str
    asset_class: str = "forex"
    read_only: bool = True
    analysis_timestamp: datetime
    timeframe: str
    bars_analyzed: int
    market_structure: dict[str, Any]
    indicators: ForexIndicatorSnapshot
    current_feature: ForexFeatureVector
    feature_store_key: str
    feature_vector_id: str | None = None
    feature_vector_ids: list[str] = Field(default_factory=list)
    feature_version: str = "fx-feature-v2"
    engine_version: str = "fx-intelligence-v2"
    provider: str = "unknown"
    provider_symbol: str | None = None
    data_status: str = "unknown"
    freshness: str = "unknown"
    feature_count: int
    confluence: dict[str, Any]
    regime: dict[str, Any]
    trade_explanation: dict[str, Any]
    overlays: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

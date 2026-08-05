from __future__ import annotations

from pydantic import BaseModel, Field


class StrategyEngineLimits(BaseModel):
    max_nesting: int = Field(default=8, ge=1, le=32)
    max_conditions: int = Field(default=64, ge=1, le=512)
    max_lookback: int = Field(default=500, ge=1, le=10000)
    max_expression_length: int = Field(default=200, ge=20, le=2000)


class StrategyEngineConfig(BaseModel):
    engine_name: str = "bensim-strategy"
    version: str = "1.0"
    use_completed_bars_only: bool = True
    limits: StrategyEngineLimits = Field(default_factory=StrategyEngineLimits)

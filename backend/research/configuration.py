from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PromotionGates(BaseModel):
    minimum_trades: int = 1
    maximum_drawdown: float = 0.25
    minimum_profit_factor: float = 1.0
    minimum_walk_forward_folds: int = 1
    minimum_positive_folds: int = 1
    maximum_train_validation_degradation: float = 0.75
    require_cost_stress_pass: bool = False
    require_monte_carlo_pass: bool = False
    require_reproducible_run: bool = True


class ResearchLimits(BaseModel):
    maximum_bars: int = 100_000
    maximum_trials: int = 500
    maximum_workers: int = 4
    maximum_artifact_bytes: int = 5_000_000


class ResearchConfig(BaseModel):
    artifact_root: Path = Field(default_factory=lambda: Path("data/research/artifacts"))
    storage_path: Path = Field(default_factory=lambda: Path("data/research/research_store.json"))
    promotion: PromotionGates = Field(default_factory=PromotionGates)
    limits: ResearchLimits = Field(default_factory=ResearchLimits)

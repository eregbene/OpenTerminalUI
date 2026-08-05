from __future__ import annotations

from dataclasses import dataclass

from backend.intelligence.trading.strategies import StrategyOutput


@dataclass(frozen=True)
class StrategyConsensus:
    agreement_score: float
    bull_score: float
    bear_score: float
    conflicting_strategies: list[str]
    recommended_direction: str
    eligible_strategy_count: int

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


def build_consensus(outputs: list[StrategyOutput]) -> StrategyConsensus:
    return build_weighted_consensus(outputs)


def build_weighted_consensus(outputs: list[StrategyOutput], weights: dict[str, float] | None = None) -> StrategyConsensus:
    weights = weights or {}
    valid = [row for row in outputs if row.valid and row.decision in {"LONG", "SHORT"} and row.confidence > 0 and row.risk_reward >= 1.0]
    bull = sum(row.confidence * _weight_for(row.strategy, weights) for row in valid if row.decision == "LONG")
    bear = sum(row.confidence * _weight_for(row.strategy, weights) for row in valid if row.decision == "SHORT")
    total = bull + bear
    if total <= 0:
        return StrategyConsensus(agreement_score=0.0, bull_score=0.0, bear_score=0.0, conflicting_strategies=[], recommended_direction="NO_TRADE", eligible_strategy_count=0)
    recommended = "LONG" if bull > bear else ("SHORT" if bear > bull else "NO_TRADE")
    agreement = max(bull, bear) / total
    conflicts = [row.strategy for row in valid if row.decision != recommended] if recommended != "NO_TRADE" else [row.strategy for row in valid]
    return StrategyConsensus(
        agreement_score=round(agreement * min(1.0, len(valid) / 4), 4),
        bull_score=round(bull / max(1, len(valid)), 4),
        bear_score=round(bear / max(1, len(valid)), 4),
        conflicting_strategies=conflicts,
        recommended_direction=recommended,
        eligible_strategy_count=len(valid),
    )


def _weight_for(strategy: str, weights: dict[str, float]) -> float:
    key = strategy.strip().lower().replace(" ", "_").replace("/", "_")
    try:
        return max(0.1, float(weights.get(key, weights.get(strategy, 1.0))))
    except (TypeError, ValueError):
        return 1.0

from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.strategies.models import Direction, StrategyContext, StrategyDecision, StrategySpec, TargetLevel, TradeProposal, stable_id


def build_trade_proposal(spec: StrategySpec, context: StrategyContext, decision: StrategyDecision) -> TradeProposal | None:
    if decision.direction not in {Direction.LONG, Direction.SHORT, "long", "short"}:
        return None
    direction = Direction(decision.direction)
    close = _float(context.features.get("price.close"))
    if close is None:
        return None
    invalidation = _invalidation_price(spec, context, close, direction)
    targets = _targets(spec, context, close, invalidation, direction)
    proposal_id = stable_id("prop", decision.decision_id, close, invalidation, len(targets))
    return TradeProposal(
        proposal_id=proposal_id,
        strategy_decision_id=decision.decision_id,
        instrument_id=context.instrument_id,
        asset_class=context.asset_class,
        direction=direction,
        proposal_time=context.as_of_timestamp,
        reference_price=close,
        entry_intent="propose_only",
        entry_price_reference="price.close",
        invalidation_price=invalidation,
        target_levels=targets,
        sizing_intent=spec.sizing_intent,
        expiry_time=context.as_of_timestamp + timedelta(days=5),
        rationale=f"{spec.strategy.name} generated a deterministic {direction.value} proposal.",
        quality_score=decision.quality_score,
        data_quality=context.data_quality,
    )


def _invalidation_price(spec: StrategySpec, context: StrategyContext, close: float, direction: Direction) -> float | None:
    atr = _float(context.features.get("indicator.atr.14")) or max(abs(close) * 0.01, 0.0001)
    rule = spec.invalidation
    if rule.type == "fixed_price":
        return rule.value
    if rule.type == "feature":
        value = _float(context.features.get(rule.reference))
        if value is None:
            return None
        return value - (rule.buffer_atr * atr) if direction == Direction.LONG else value + (rule.buffer_atr * atr)
    distance = (rule.value or 1.5) * atr
    return close - distance if direction == Direction.LONG else close + distance


def _targets(spec: StrategySpec, context: StrategyContext, close: float, invalidation: float | None, direction: Direction) -> list[TargetLevel]:
    levels: list[TargetLevel] = []
    risk = abs(close - invalidation) if invalidation is not None else max(abs(close) * 0.01, 0.0001)
    for rule in spec.targets:
        if rule.type == "fixed_price":
            price = rule.value
            rationale = "fixed target"
        elif rule.type == "feature" and rule.reference:
            price = _float(context.features.get(rule.reference)) or (close + risk if direction == Direction.LONG else close - risk)
            rationale = f"feature target from {rule.reference}"
        else:
            multiple = rule.value or 2.0
            price = close + (risk * multiple) if direction == Direction.LONG else close - (risk * multiple)
            rationale = f"{multiple}:1 risk reward"
        levels.append(TargetLevel(type=rule.type, price=price, reference=rule.reference, rationale=rationale))
    return levels


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.strategies.conditions import ALLOWED_OPERATORS, is_known_feature
from backend.strategies.configuration import StrategyEngineLimits
from backend.strategies.models import Condition, ConditionResult, EvaluationResult, GroupResult, RuleGroup, RuleNode, StrategyContext, stable_id


@dataclass(frozen=True)
class RuleComplexity:
    conditions: int = 0
    depth: int = 0


def validate_rule_tree(node: RuleNode | None, limits: StrategyEngineLimits, depth: int = 1) -> RuleComplexity:
    if node is None:
        return RuleComplexity(0, depth)
    if depth > limits.max_nesting:
        raise ValueError(f"rule nesting exceeds limit {limits.max_nesting}")
    if isinstance(node, Condition):
        if node.operator not in ALLOWED_OPERATORS:
            raise ValueError(f"operator is not allowed: {node.operator}")
        if not is_known_feature(node.feature):
            raise ValueError(f"unknown feature namespace: {node.feature}")
        if len(node.feature) > limits.max_expression_length:
            raise ValueError("feature reference is too long")
        return RuleComplexity(1, depth)
    children: list[RuleNode] = []
    if node.all is not None:
        children = node.all
    elif node.any is not None:
        children = node.any
    elif node.not_ is not None:
        children = [node.not_]
    total = 0
    max_depth = depth
    for child in children:
        child_complexity = validate_rule_tree(child, limits, depth + 1)
        total += child_complexity.conditions
        max_depth = max(max_depth, child_complexity.depth)
    if total > limits.max_conditions:
        raise ValueError(f"condition count exceeds limit {limits.max_conditions}")
    return RuleComplexity(total, max_depth)


def evaluate_rule(node: RuleNode, context: StrategyContext) -> EvaluationResult:
    if isinstance(node, Condition):
        return evaluate_condition(node, context)
    evaluated_at = context.as_of_timestamp
    if node.all is not None:
        children = [evaluate_rule(child, context) for child in node.all]
        return GroupResult(group_id=node.id or stable_id("grp", "all", evaluated_at, len(children)), operator="all", result=all(_result(c) for c in children), child_results=children)
    if node.any is not None:
        children = [evaluate_rule(child, context) for child in node.any]
        return GroupResult(group_id=node.id or stable_id("grp", "any", evaluated_at, len(children)), operator="any", result=any(_result(c) for c in children), child_results=children)
    assert node.not_ is not None
    child = evaluate_rule(node.not_, context)
    return GroupResult(group_id=node.id or stable_id("grp", "not", evaluated_at), operator="not", result=not _result(child), child_results=[child])


def evaluate_condition(condition: Condition, context: StrategyContext) -> ConditionResult:
    observed = context.features.get(condition.feature)
    expected = _resolve_expected(condition.value, context)
    result, reason = _apply_operator(observed, condition.operator, expected)
    return ConditionResult(
        condition_id=condition.id or stable_id("cond", condition.feature, condition.operator, condition.value),
        feature=condition.feature,
        operator=condition.operator,
        expected_value=expected,
        observed_value=observed,
        result=result,
        evaluated_at=context.as_of_timestamp,
        source_timeframe=condition.timeframe or context.execution_timeframe,
        source_timestamp=context.feature_timestamps.get(condition.feature),
        reason=reason,
    )


def _result(result: EvaluationResult) -> bool:
    return bool(getattr(result, "result", False))


def _apply_operator(observed: Any, operator: str, expected: Any) -> tuple[bool, str]:
    if operator == "exists":
        return observed is not None, "feature exists" if observed is not None else "feature is unavailable"
    if observed is None:
        return False, "feature is unavailable"
    if operator == "is_true":
        return observed is True, f"observed {observed!r}"
    if operator == "is_false":
        return observed is False, f"observed {observed!r}"
    if operator == "eq":
        return observed == expected, f"observed {observed!r}, expected {expected!r}"
    if operator == "neq":
        return observed != expected, f"observed {observed!r}, expected not {expected!r}"
    if operator in {"gt", "gte", "lt", "lte"}:
        left = _as_float(observed)
        right = _as_float(expected)
        if left is None or right is None:
            return False, "numeric comparison requires numeric values"
        if operator == "gt":
            return left > right, f"{left} > {right}"
        if operator == "gte":
            return left >= right, f"{left} >= {right}"
        if operator == "lt":
            return left < right, f"{left} < {right}"
        return left <= right, f"{left} <= {right}"
    if operator == "between":
        left = _as_float(observed)
        if left is None or not isinstance(expected, list | tuple) or len(expected) != 2:
            return False, "between requires [low, high]"
        low = _as_float(expected[0])
        high = _as_float(expected[1])
        if low is None or high is None:
            return False, "between requires numeric bounds"
        return low <= left <= high, f"{low} <= {left} <= {high}"
    if operator == "in":
        values = expected if isinstance(expected, list | tuple | set) else [expected]
        return observed in values, f"observed {observed!r} in {list(values)!r}"
    if operator == "not_in":
        values = expected if isinstance(expected, list | tuple | set) else [expected]
        return observed not in values, f"observed {observed!r} not in {list(values)!r}"
    return False, f"operator is not implemented: {operator}"


def _resolve_expected(value: Any, context: StrategyContext) -> Any:
    if isinstance(value, str) and is_known_feature(value):
        return context.features.get(value)
    if isinstance(value, list):
        return [_resolve_expected(item, context) for item in value]
    return value


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

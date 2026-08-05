from __future__ import annotations

from typing import Any

from backend.market_data.models import DataStatus
from backend.strategies.configuration import StrategyEngineConfig
from backend.strategies.evaluator import evaluate_rule, validate_rule_tree
from backend.strategies.events import decision_event, proposal_event
from backend.strategies.inputs import build_context
from backend.strategies.models import (
    DecisionType,
    Direction,
    EvaluationResult,
    StrategyContext,
    StrategyDecision,
    StrategyEvaluation,
    StrategySpec,
    StrategyState,
    stable_id,
)
from backend.strategies.proposals import build_trade_proposal


class StrategyEngine:
    def __init__(self, config: StrategyEngineConfig | None = None) -> None:
        self.config = config or StrategyEngineConfig()

    def compile(self, spec: StrategySpec) -> StrategySpec:
        if spec.entry.long is None and spec.entry.short is None:
            raise ValueError("strategy requires at least one entry rule")
        validate_rule_tree(spec.entry.long, self.config.limits)
        validate_rule_tree(spec.entry.short, self.config.limits)
        validate_rule_tree(spec.exit.long, self.config.limits)
        validate_rule_tree(spec.exit.short, self.config.limits)
        if spec.required_history > self.config.limits.max_lookback:
            raise ValueError("required history exceeds engine lookback limit")
        return spec

    def evaluate_context(self, spec: StrategySpec, context: StrategyContext, state: StrategyState | None = None) -> StrategyEvaluation:
        spec = self.compile(spec)
        warnings = self._data_policy_warnings(spec, context)
        next_state = state or StrategyState(strategy_id=spec.strategy.id, strategy_hash=spec.strategy_hash(), instrument_id=context.instrument_id)
        if warnings:
            decision = self._decision(spec, context, DecisionType.BLOCKED, Direction.NEUTRAL, None, "; ".join(warnings), quality=0)
            return self._evaluation(spec, context, next_state, [decision], warnings)
        if len(context.completed_bars) < spec.required_history:
            decision = self._decision(spec, context, DecisionType.INSUFFICIENT_DATA, Direction.NEUTRAL, None, f"requires {spec.required_history} completed bars", quality=0)
            return self._evaluation(spec, context, next_state, [decision], warnings)
        if self._cooling_down(spec, context, next_state):
            decision = self._decision(spec, context, DecisionType.NO_ACTION, Direction.NEUTRAL, None, "strategy cooldown is active", quality=1)
            return self._evaluation(spec, context, next_state, [decision], warnings)

        long_result = evaluate_rule(spec.entry.long, context) if spec.entry.long is not None else None
        short_result = evaluate_rule(spec.entry.short, context) if spec.entry.short is not None else None
        long_ok = _rule_ok(long_result)
        short_ok = _rule_ok(short_result)
        if long_ok and not short_ok:
            decision = self._decision(spec, context, DecisionType.LONG, Direction.LONG, long_result, "long entry rules passed")
        elif short_ok and not long_ok:
            decision = self._decision(spec, context, DecisionType.SHORT, Direction.SHORT, short_result, "short entry rules passed")
        elif long_ok and short_ok:
            decision = self._decision(spec, context, DecisionType.BLOCKED, Direction.NEUTRAL, long_result, "long and short rules both passed; deterministic conflict")
        else:
            decision = self._decision(spec, context, DecisionType.NO_ACTION, Direction.NEUTRAL, long_result or short_result, "no entry rules passed")

        proposal = build_trade_proposal(spec, context, decision)
        if proposal is not None:
            decision.proposal = proposal
            next_state.active_proposal_ids.append(proposal.proposal_id)
            next_state.last_proposal_bar_index = len(context.completed_bars) - 1
            next_state.state = "proposal_active"
            next_state.transitions.append({"at": context.as_of_timestamp.isoformat(), "to": "proposal_active", "proposal_id": proposal.proposal_id})
        next_state.last_decision_time = context.as_of_timestamp
        return self._evaluation(spec, context, next_state, [decision], warnings)

    def evaluate_bars(self, spec: StrategySpec, bars: list[dict[str, Any]], *, symbol: str, **kwargs: Any) -> StrategyEvaluation:
        context = build_context(spec, bars, symbol=symbol, **kwargs)
        return self.evaluate_context(spec, context)

    def evaluate_batch(self, spec: StrategySpec, bars: list[dict[str, Any]], *, symbol: str, **kwargs: Any) -> StrategyEvaluation:
        state: StrategyState | None = None
        final: StrategyEvaluation | None = None
        start = max(1, spec.required_history)
        for idx in range(start, len(bars) + 1):
            final = self.evaluate_bars(spec, bars[:idx], symbol=symbol, **kwargs)
            state = final.state
        if final is None:
            return self.evaluate_bars(spec, bars, symbol=symbol, **kwargs)
        return final

    def _decision(
        self,
        spec: StrategySpec,
        context: StrategyContext,
        decision_type: DecisionType,
        direction: Direction,
        rule_result: EvaluationResult | None,
        explanation: str,
        *,
        quality: float = 1.0,
    ) -> StrategyDecision:
        decision_id = stable_id("dec", spec.strategy.id, spec.strategy_hash(), context.instrument_id, context.as_of_timestamp, decision_type)
        return StrategyDecision(
            decision_id=decision_id,
            strategy_id=spec.strategy.id,
            strategy_version=spec.strategy.version,
            instrument_id=context.instrument_id,
            symbol=context.symbol,
            timeframe=context.execution_timeframe,
            as_of_timestamp=context.as_of_timestamp,
            direction=direction,
            decision_type=decision_type,
            rule_result=rule_result,
            quality_score=quality,
            explanation=explanation,
            input_references={"completed_bars": len(context.completed_bars), "features": sorted(context.features.keys())},
            dataset_snapshot_id=context.dataset_snapshot_id,
            configuration_hash=spec.strategy_hash(),
            data_quality=context.data_quality,
        )

    def _evaluation(self, spec: StrategySpec, context: StrategyContext, state: StrategyState, decisions: list[StrategyDecision], warnings: list[str]) -> StrategyEvaluation:
        proposals = [decision.proposal for decision in decisions if decision.proposal is not None]
        events = [decision_event(decision) for decision in decisions]
        for decision in decisions:
            if decision.proposal is not None:
                events.append(proposal_event(decision, decision.proposal))
        return StrategyEvaluation(
            evaluation_id=stable_id("eval", spec.strategy.id, spec.strategy_hash(), context.instrument_id, context.as_of_timestamp),
            strategy_id=spec.strategy.id,
            strategy_version=spec.strategy.version,
            strategy_hash=spec.strategy_hash(),
            symbol=context.symbol,
            timeframe=context.execution_timeframe,
            dataset_snapshot_id=context.dataset_snapshot_id,
            decisions=decisions,
            proposals=proposals,
            state=state,
            events=events,
            warnings=warnings,
            feature_rows=[{"as_of_timestamp": context.as_of_timestamp.isoformat(), **context.features}],
        )

    def _data_policy_warnings(self, spec: StrategySpec, context: StrategyContext) -> list[str]:
        warnings: list[str] = []
        quality = context.data_quality
        if quality is None:
            return warnings
        statuses = {str(status.value if isinstance(status, DataStatus) else status) for status in quality.status}
        if not spec.data_policy.allow_simulated and quality.is_simulated:
            warnings.append("simulated data is blocked by strategy policy")
        if not spec.data_policy.allow_delayed and "delayed" in statuses:
            warnings.append("delayed data is blocked by strategy policy")
        if not spec.data_policy.allow_cached and "cached" in statuses:
            warnings.append("cached data is blocked by strategy policy")
        if not spec.data_policy.allow_fallback and "fallback" in statuses:
            warnings.append("fallback data is blocked by strategy policy")
        if spec.data_policy.minimum_quality_score is not None and quality.quality_score is not None and quality.quality_score < spec.data_policy.minimum_quality_score:
            warnings.append("data quality score is below strategy policy")
        return warnings

    def _cooling_down(self, spec: StrategySpec, context: StrategyContext, state: StrategyState) -> bool:
        if spec.cooldown.bars <= 0 or state.last_proposal_bar_index is None:
            return False
        return len(context.completed_bars) - 1 - state.last_proposal_bar_index < spec.cooldown.bars


def evaluate_strategy(spec: StrategySpec, bars: list[dict[str, Any]], *, symbol: str, **kwargs: Any) -> StrategyEvaluation:
    return StrategyEngine().evaluate_bars(spec, bars, symbol=symbol, **kwargs)


def _rule_ok(result: EvaluationResult | None) -> bool:
    return bool(getattr(result, "result", False))

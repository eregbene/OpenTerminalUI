from __future__ import annotations

from backend.strategies.models import StrategyEvaluation


def decisions_to_backtest_signals(evaluation: StrategyEvaluation) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    for decision in evaluation.decisions:
        signals.append(
            {
                "timestamp": decision.as_of_timestamp.isoformat(),
                "symbol": decision.symbol,
                "strategy_id": decision.strategy_id,
                "decision_type": decision.decision_type,
                "direction": decision.direction,
                "quality_score": decision.quality_score,
                "proposal": decision.proposal.model_dump(mode="json") if decision.proposal else None,
            }
        )
    return signals

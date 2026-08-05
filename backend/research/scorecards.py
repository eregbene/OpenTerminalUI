from __future__ import annotations

from backend.research.configuration import PromotionGates
from backend.research.models import BacktestRun, OptimizationJob, ResearchCandidate, StrategyScorecard, CandidateStatus, ValidationJob, stable_id, utc_now
from backend.research.optimization import optimization_result


def build_scorecard(backtest: BacktestRun, optimization: OptimizationJob, validation: ValidationJob, gates: PromotionGates) -> StrategyScorecard:
    metrics = backtest.result.metrics
    opt = optimization_result(optimization)
    positive_folds = sum(1 for fold in validation.folds if fold.validation_metrics and fold.validation_metrics.total_return > 0)
    avg_degradation = _avg([fold.degradation for fold in validation.folds if fold.degradation is not None])
    gate_results = {
        "minimum_trades": metrics.trade_count >= gates.minimum_trades,
        "maximum_drawdown": abs(metrics.maximum_drawdown) <= gates.maximum_drawdown,
        "minimum_profit_factor": (metrics.profit_factor or 0) >= gates.minimum_profit_factor,
        "minimum_walk_forward_folds": len(validation.folds) >= gates.minimum_walk_forward_folds,
        "minimum_positive_folds": positive_folds >= gates.minimum_positive_folds,
        "maximum_train_validation_degradation": avg_degradation is None or avg_degradation <= gates.maximum_train_validation_degradation,
        "require_reproducible_run": bool(backtest.lineage.dataset_hash and backtest.lineage.strategy_hash),
    }
    components = {
        "profitability": _clip(metrics.total_return + 0.5),
        "risk": _clip(1 - abs(metrics.maximum_drawdown)),
        "consistency": _clip((metrics.win_rate or 0.0)),
        "sample_adequacy": _clip(metrics.trade_count / max(gates.minimum_trades, 1)),
        "parameter_stability": _clip(1 - len(opt.warnings) * 0.2),
        "walk_forward": _clip(positive_folds / max(len(validation.folds), 1)),
        "robustness": _clip(sum(1 for row in validation.robustness if row.passed) / max(len(validation.robustness), 1)),
        "data_quality": 1.0,
        "reproducibility": 1.0,
    }
    total = sum(components.values()) / len(components)
    warnings = [*metrics.warnings, *opt.warnings]
    return StrategyScorecard(
        scorecard_id=stable_id("score", backtest.run_id, optimization.job_id, validation.job_id),
        strategy_id=backtest.lineage.strategy_id,
        strategy_version=backtest.lineage.strategy_version,
        total_score=total,
        components=components,
        gates=gate_results,
        passed=all(gate_results.values()),
        warnings=warnings,
        lineage=backtest.lineage,
        created_at=utc_now(),
    )


def build_candidate(scorecard: StrategyScorecard, optimization: OptimizationJob, validation: ValidationJob, selected_parameters: dict, dataset_scope: dict) -> ResearchCandidate:
    failed = [name for name, passed in scorecard.gates.items() if not passed]
    return ResearchCandidate(
        candidate_id=stable_id("cand", scorecard.scorecard_id),
        strategy_id=scorecard.strategy_id,
        strategy_version=scorecard.strategy_version,
        selected_parameters=selected_parameters,
        dataset_scope=dataset_scope,
        optimization_job_id=optimization.job_id,
        validation_job_id=validation.job_id,
        scorecard_id=scorecard.scorecard_id,
        promotion_status=CandidateStatus.QUALIFIED if scorecard.passed else CandidateStatus.REJECTED,
        promotion_reasons=[name for name, passed in scorecard.gates.items() if passed],
        rejection_reasons=failed,
    )


def mark_stale(candidate: ResearchCandidate, current_hashes: dict[str, str], scorecard: StrategyScorecard) -> ResearchCandidate:
    stale = current_hashes.get("strategy_hash", scorecard.lineage.strategy_hash) != scorecard.lineage.strategy_hash
    return candidate.model_copy(update={"stale": stale, "promotion_status": CandidateStatus.EXPIRED if stale else candidate.promotion_status})


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None

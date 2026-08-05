from __future__ import annotations

import itertools
import random
from typing import Any

from backend.research.backtests import EventDrivenBacktester
from backend.research.models import (
    BacktestConfiguration,
    DatasetSnapshot,
    OptimizationJob,
    OptimizationResult,
    OptimizationTrial,
    ParameterSpace,
    ResearchStatus,
    parameter_values,
    stable_hash,
    stable_id,
    utc_now,
)
from backend.strategies.models import StrategySpec


def apply_parameter_set(spec: StrategySpec, params: dict[str, Any]) -> StrategySpec:
    payload = spec.model_dump(mode="json", by_alias=True)
    for path, value in params.items():
        _set_path(payload, path, value)
    return StrategySpec.model_validate(payload)


def run_optimization(
    spec: StrategySpec,
    dataset: DatasetSnapshot,
    config: BacktestConfiguration,
    space: ParameterSpace,
    *,
    method: str = "grid",
    objective: str = "sharpe_ratio",
    max_trials: int = 100,
    random_seed: int = 42,
) -> OptimizationJob:
    combinations = _combinations(space)
    if len(combinations) > space.max_combinations:
        raise ValueError("parameter space exceeds configured max combinations")
    if method == "random":
        rng = random.Random(random_seed)
        rng.shuffle(combinations)
    combinations = combinations[:max_trials]
    lineage = EventDrivenBacktester().run(spec, dataset, config, parameter_set={}, random_seed=random_seed).lineage
    job = OptimizationJob(
        run_id=stable_id("opt", lineage.correlation_id, method, max_trials),
        job_id=stable_id("opt", lineage.correlation_id, method, max_trials),
        status=ResearchStatus.RUNNING,
        started_at=utc_now(),
        lineage=lineage,
        method=method,  # type: ignore[arg-type]
        objective=objective,
        parameter_space=space,
        max_trials=max_trials,
    )
    seen: set[str] = set()
    for params in combinations:
        param_hash = stable_hash(params)
        if param_hash in seen:
            continue
        seen.add(param_hash)
        trial = OptimizationTrial(trial_id=stable_id("trial", job.job_id, param_hash), job_id=job.job_id, parameter_set=params, parameter_set_hash=param_hash, status=ResearchStatus.RUNNING)
        try:
            trial_spec = apply_parameter_set(spec, params)
            backtest = EventDrivenBacktester().run(trial_spec, dataset, config, parameter_set=params, random_seed=random_seed)
            trial.metrics = backtest.result.metrics if backtest.result else None
            trial.objective_value = _objective_value(trial.metrics, objective)
            trial.status = ResearchStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            trial.status = ResearchStatus.FAILED
            trial.errors.append(str(exc))
        trial.completed_at = utc_now()
        job.trials.append(trial)
    job.status = ResearchStatus.COMPLETED
    job.completed_at = utc_now()
    return job


def optimization_result(job: OptimizationJob) -> OptimizationResult:
    ranked = sorted([t for t in job.trials if t.status == ResearchStatus.COMPLETED], key=lambda t: t.objective_value if t.objective_value is not None else float("-inf"), reverse=True)
    failed = [t for t in job.trials if t.status == ResearchStatus.FAILED]
    warnings = []
    if len(job.parameter_space.parameters) > 5:
        warnings.append("large parameter count increases overfitting risk")
    if ranked and ranked[0].metrics and ranked[0].metrics.trade_count < 10:
        warnings.append("best trial has low trade count")
    return OptimizationResult(job_id=job.job_id, ranked_trials=ranked, failed_trials=failed, pareto_frontier=ranked[:5], warnings=warnings)


def _combinations(space: ParameterSpace) -> list[dict[str, Any]]:
    if not space.parameters:
        return [{}]
    values = [parameter_values(param) for param in space.parameters]
    out = []
    for combo in itertools.product(*values):
        out.append({param.path: value for param, value in zip(space.parameters, combo)})
    return out


def _objective_value(metrics, objective: str) -> float | None:
    if metrics is None:
        return None
    value = getattr(metrics, objective, None)
    return float(value) if value is not None else None


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    allowed = {"invalidation.value", "targets.0.value", "cooldown.bars", "data_policy.minimum_quality_score", "required_history"}
    if path not in allowed:
        raise ValueError(f"parameter path is not allowed: {path}")
    parts = path.split(".")
    node: Any = payload
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    last = parts[-1]
    if last.isdigit():
        node[int(last)] = value
    else:
        node[last] = value

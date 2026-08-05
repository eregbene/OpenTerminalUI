from __future__ import annotations

import random

from backend.research.backtests import EventDrivenBacktester
from backend.research.models import (
    BacktestConfiguration,
    CostModel,
    DatasetSnapshot,
    RobustnessResult,
    ValidationJob,
    ValidationResult,
    ValidationScenario,
    WalkForwardFold,
    WalkForwardPlan,
    ResearchStatus,
    stable_id,
    utc_now,
)
from backend.research.optimization import apply_parameter_set
from backend.strategies.models import StrategySpec


def run_walk_forward(spec: StrategySpec, dataset: DatasetSnapshot, config: BacktestConfiguration, plan: WalkForwardPlan, *, selected_parameters: dict | None = None) -> list[WalkForwardFold]:
    selected_parameters = selected_parameters or {}
    folds: list[WalkForwardFold] = []
    bars = dataset.bars
    start = 0
    while start + plan.train_bars + plan.purge_bars + plan.embargo_bars + plan.validation_bars <= len(bars):
        train_start = start if plan.mode == "rolling" else 0
        train_end = start + plan.train_bars
        val_start = train_end + plan.purge_bars + plan.embargo_bars
        val_end = val_start + plan.validation_bars
        train_ds = dataset.model_copy(update={"bars": bars[train_start:train_end], "dataset_snapshot_id": stable_id("ds", dataset.dataset_snapshot_id, "train", start)})
        val_ds = dataset.model_copy(update={"bars": bars[val_start:val_end], "dataset_snapshot_id": stable_id("ds", dataset.dataset_snapshot_id, "val", start)})
        train = EventDrivenBacktester().run(apply_parameter_set(spec, selected_parameters), train_ds, config, parameter_set=selected_parameters)
        val = EventDrivenBacktester().run(apply_parameter_set(spec, selected_parameters), val_ds, config, parameter_set=selected_parameters)
        train_metric = train.result.metrics if train.result else None
        val_metric = val.result.metrics if val.result else None
        train_obj = train_metric.sharpe_ratio if train_metric and train_metric.sharpe_ratio is not None else 0.0
        val_obj = val_metric.sharpe_ratio if val_metric and val_metric.sharpe_ratio is not None else 0.0
        folds.append(
            WalkForwardFold(
                fold_id=stable_id("fold", dataset.dataset_snapshot_id, start),
                train_start=_ts(bars[train_start]),
                train_end=_ts(bars[train_end - 1]),
                validation_start=_ts(bars[val_start]),
                validation_end=_ts(bars[val_end - 1]),
                selected_parameters=selected_parameters,
                train_metrics=train_metric,
                validation_metrics=val_metric,
                degradation=(train_obj - val_obj) / abs(train_obj) if train_obj else None,
            )
        )
        start += plan.step_bars
    return folds


def run_robustness(spec: StrategySpec, dataset: DatasetSnapshot, config: BacktestConfiguration, *, random_seed: int = 42) -> list[RobustnessResult]:
    scenarios = [
        ValidationScenario(scenario_id="cost_stress", name="Cost stress", parameters={"slippage_bps": config.cost_model.slippage_bps + 10}),
        ValidationScenario(scenario_id="entry_delay", name="Entry delay", parameters={"entry_delay_bars": config.execution_model.entry_delay_bars + 1}),
        ValidationScenario(scenario_id="skipped_trades", name="Random skipped trades", parameters={"skip_every": 3}),
    ]
    results: list[RobustnessResult] = []
    for scenario in scenarios:
        scenario_config = config.model_copy(deep=True)
        scenario_dataset = dataset
        if scenario.scenario_id == "cost_stress":
            scenario_config.cost_model = CostModel(**{**scenario_config.cost_model.model_dump(), "slippage_bps": scenario.parameters["slippage_bps"]})
        if scenario.scenario_id == "entry_delay":
            scenario_config.execution_model.entry_delay_bars = scenario.parameters["entry_delay_bars"]
        if scenario.scenario_id == "skipped_trades":
            rng = random.Random(random_seed)
            bars = [bar for idx, bar in enumerate(dataset.bars) if idx % int(scenario.parameters["skip_every"]) != 0 or rng.random() > 0.5]
            scenario_dataset = dataset.model_copy(update={"bars": bars, "dataset_snapshot_id": stable_id("ds", dataset.dataset_snapshot_id, scenario.scenario_id)})
        backtest = EventDrivenBacktester().run(spec, scenario_dataset, scenario_config)
        metrics = backtest.result.metrics
        results.append(RobustnessResult(scenario_id=scenario.scenario_id, name=scenario.name, metrics=metrics, passed=metrics.maximum_drawdown >= -0.5, warnings=metrics.warnings))
    return results


def run_validation_job(spec: StrategySpec, dataset: DatasetSnapshot, config: BacktestConfiguration, plan: WalkForwardPlan, *, selected_parameters: dict | None = None) -> ValidationJob:
    base = EventDrivenBacktester().run(spec, dataset, config, parameter_set=selected_parameters or {})
    job = ValidationJob(
        run_id=stable_id("val", base.lineage.correlation_id),
        job_id=stable_id("val", base.lineage.correlation_id),
        status=ResearchStatus.RUNNING,
        started_at=utc_now(),
        lineage=base.lineage,
        plan=plan,
    )
    job.folds = run_walk_forward(spec, dataset, config, plan, selected_parameters=selected_parameters)
    job.robustness = run_robustness(spec, dataset, config)
    job.status = ResearchStatus.COMPLETED
    job.completed_at = utc_now()
    return job


def validation_result(job: ValidationJob) -> ValidationResult:
    positive = sum(1 for fold in job.folds if fold.validation_metrics and fold.validation_metrics.total_return > 0)
    passed = bool(job.folds) and positive >= max(1, len(job.folds) // 2) and all(row.passed for row in job.robustness)
    warnings = []
    if not job.folds:
        warnings.append("no walk-forward folds were produced")
    return ValidationResult(validation_job_id=job.job_id, folds=job.folds, robustness=job.robustness, passed=passed, warnings=warnings)


def _ts(bar: dict) -> object:
    from datetime import datetime

    value = bar.get("timestamp") or bar.get("close_time") or bar.get("time")
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))

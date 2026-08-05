from __future__ import annotations

from backend.research.models import BacktestConfiguration, DatasetSnapshot, LineageRecord, stable_hash, stable_id
from backend.strategies.models import StrategySpec


def build_lineage(
    spec: StrategySpec,
    dataset: DatasetSnapshot,
    backtest_config: BacktestConfiguration,
    parameter_set: dict,
    *,
    random_seed: int = 42,
    created_by: str = "research",
) -> LineageRecord:
    parameter_hash = stable_hash(parameter_set)
    return LineageRecord(
        strategy_id=spec.strategy.id,
        strategy_version=spec.strategy.version,
        strategy_hash=spec.strategy_hash(),
        dataset_snapshot_id=dataset.dataset_snapshot_id,
        dataset_hash=dataset.dataset_hash(),
        execution_model_version=backtest_config.execution_model.version,
        cost_model_version=backtest_config.cost_model.version,
        parameter_set_hash=parameter_hash,
        random_seed=random_seed,
        created_by=created_by,
        correlation_id=stable_id("corr", spec.strategy.id, dataset.dataset_snapshot_id, parameter_hash, random_seed),
    )

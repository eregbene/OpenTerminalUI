from __future__ import annotations

from backend.research.models import WorkflowResult


def reproducibility_manifest(result: WorkflowResult) -> dict[str, object]:
    lineage = result.backtest.lineage
    return {
        "application": "Bensim Trading",
        "python_version": "3.11",
        "strategy_id": lineage.strategy_id,
        "strategy_version": lineage.strategy_version,
        "strategy_hash": lineage.strategy_hash,
        "dataset_snapshot_id": lineage.dataset_snapshot_id,
        "dataset_hash": lineage.dataset_hash,
        "market_structure_config_hash": lineage.market_structure_config_hash,
        "feature_version": lineage.feature_version,
        "backtest_engine_version": lineage.backtest_engine_version,
        "execution_model_version": lineage.execution_model_version,
        "cost_model_version": lineage.cost_model_version,
        "parameter_set_hash": lineage.parameter_set_hash,
        "random_seed": lineage.random_seed,
        "optimization_job_id": result.optimization.job_id,
        "validation_job_id": result.validation.job_id,
        "scorecard_id": result.scorecard.scorecard_id,
        "candidate_id": result.candidate.candidate_id,
        "artifact_hashes": [artifact.content_hash for artifact in result.backtest.artifacts],
    }

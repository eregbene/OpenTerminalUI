from __future__ import annotations

from typing import Any

from backend.research.artifacts import ArtifactStore
from backend.research.backtests import EventDrivenBacktester
from backend.research.configuration import ResearchConfig
from backend.research.events import research_event
from backend.research.models import (
    BacktestConfiguration,
    BacktestRun,
    CandidateStatus,
    DatasetSnapshot,
    OptimizationJob,
    ParameterSpace,
    ResearchCandidate,
    ResearchExperiment,
    ResearchStatus,
    StrategyScorecard,
    ValidationJob,
    WalkForwardPlan,
    WorkflowRequest,
    WorkflowResult,
    stable_id,
    utc_now,
)
from backend.research.optimization import optimization_result, run_optimization
from backend.research.registry import ResearchRegistry
from backend.research.scorecards import build_candidate, build_scorecard
from backend.research.serialization import reproducibility_manifest
from backend.research.validation import run_validation_job
from backend.strategies.registry import get_strategy


class ResearchService:
    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()
        self.registry = ResearchRegistry(self.config.storage_path)
        self.artifacts = ArtifactStore(self.config.artifact_root)

    def create_experiment(
        self,
        *,
        name: str,
        objective: str,
        hypothesis: str,
        strategy_id: str,
        strategy_version: str,
        dataset_snapshot_id: str,
        parameter_space: ParameterSpace | None = None,
        owner: str = "research",
    ) -> ResearchExperiment:
        experiment = ResearchExperiment(
            experiment_id=stable_id("exp", strategy_id, dataset_snapshot_id, name, utc_now()),
            name=name,
            objective=objective,
            hypothesis=hypothesis,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_snapshot_id=dataset_snapshot_id,
            parameter_space=parameter_space or ParameterSpace(),
            owner=owner,
        )
        self.registry.save("experiments", experiment.experiment_id, experiment.model_dump(mode="json"))
        return experiment

    def list_experiments(self) -> list[ResearchExperiment]:
        return [ResearchExperiment.model_validate(row) for row in self.registry.list("experiments")]

    def get_experiment(self, experiment_id: str) -> ResearchExperiment | None:
        row = self.registry.get("experiments", experiment_id)
        return ResearchExperiment.model_validate(row) if row else None

    def run_backtest(self, strategy_id: str, dataset: DatasetSnapshot, config: BacktestConfiguration | None = None, parameter_set: dict[str, Any] | None = None) -> BacktestRun:
        spec = get_strategy(strategy_id)
        if spec is None:
            raise ValueError("strategy not found")
        run = EventDrivenBacktester().run(spec, dataset, config or BacktestConfiguration(), parameter_set=parameter_set or {})
        artifact = self.artifacts.write_json("backtest_result", run.result.model_dump(mode="json") if run.result else {}, {"run_id": run.run_id})
        run.artifacts.append(artifact)
        self.registry.save("backtests", run.run_id, run.model_dump(mode="json"))
        self.registry.save("events", stable_id("eventlog", run.run_id), research_event("research.backtest.completed", run.lineage, job_id=run.run_id).model_dump(mode="json"))
        return run

    def run_optimization(
        self,
        strategy_id: str,
        dataset: DatasetSnapshot,
        config: BacktestConfiguration,
        space: ParameterSpace,
        *,
        method: str = "grid",
        objective: str = "sharpe_ratio",
        max_trials: int = 100,
        random_seed: int = 42,
    ) -> OptimizationJob:
        spec = get_strategy(strategy_id)
        if spec is None:
            raise ValueError("strategy not found")
        if max_trials > self.config.limits.maximum_trials:
            raise ValueError("max trials exceeds research limits")
        job = run_optimization(spec, dataset, config, space, method=method, objective=objective, max_trials=max_trials, random_seed=random_seed)
        artifact = self.artifacts.write_json("optimization_trials", [trial.model_dump(mode="json") for trial in job.trials], {"job_id": job.job_id})
        job.artifacts.append(artifact)
        self.registry.save("optimizations", job.job_id or job.run_id, job.model_dump(mode="json"))
        return job

    def run_validation(
        self,
        strategy_id: str,
        dataset: DatasetSnapshot,
        config: BacktestConfiguration,
        plan: WalkForwardPlan,
        selected_parameters: dict[str, Any] | None = None,
    ) -> ValidationJob:
        spec = get_strategy(strategy_id)
        if spec is None:
            raise ValueError("strategy not found")
        job = run_validation_job(spec, dataset, config, plan, selected_parameters=selected_parameters)
        artifact = self.artifacts.write_json("validation_result", job.model_dump(mode="json"), {"job_id": job.job_id})
        job.artifacts.append(artifact)
        self.registry.save("validations", job.job_id or job.run_id, job.model_dump(mode="json"))
        return job

    def run_workflow(self, request: WorkflowRequest) -> WorkflowResult:
        spec = get_strategy(request.strategy_id)
        if spec is None:
            raise ValueError("strategy not found")
        dataset = DatasetSnapshot(
            dataset_snapshot_id=stable_id("ds", request.symbol, request.timeframe, len(request.bars)),
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=request.bars,
        )
        experiment = self.create_experiment(
            name=f"{spec.strategy.name} research",
            objective="Evaluate deterministic strategy robustness before paper-candidate review.",
            hypothesis="Strategy should remain stable under validation and cost perturbations.",
            strategy_id=spec.strategy.id,
            strategy_version=spec.strategy.version,
            dataset_snapshot_id=dataset.dataset_snapshot_id,
            parameter_space=request.parameter_space,
        )
        backtest = self.run_backtest(spec.strategy.id, dataset, request.backtest)
        optimization = self.run_optimization(spec.strategy.id, dataset, request.backtest, request.parameter_space, max_trials=min(25, max(1, request.parameter_space.estimate_size())), random_seed=request.random_seed)
        ranked = optimization_result(optimization).ranked_trials
        selected = ranked[0].parameter_set if ranked else {}
        validation = self.run_validation(spec.strategy.id, dataset, request.backtest, request.walk_forward, selected_parameters=selected)
        scorecard = build_scorecard(backtest, optimization, validation, self.config.promotion)
        candidate = build_candidate(scorecard, optimization, validation, selected, {"symbol": dataset.symbol, "timeframe": dataset.timeframe, "dataset_snapshot_id": dataset.dataset_snapshot_id})
        events = [
            research_event("research.experiment.created", backtest.lineage, job_id=experiment.experiment_id),
            research_event("research.backtest.completed", backtest.lineage, job_id=backtest.run_id),
            research_event("research.optimization.completed", optimization.lineage, job_id=optimization.job_id),
            research_event("research.validation.completed", validation.lineage, job_id=validation.job_id),
            research_event("research.scorecard.generated", scorecard.lineage, job_id=scorecard.scorecard_id),
            research_event("research.candidate.qualified" if candidate.promotion_status == CandidateStatus.QUALIFIED else "research.candidate.rejected", scorecard.lineage, job_id=candidate.candidate_id),
        ]
        result = WorkflowResult(
            experiment=experiment,
            backtest=backtest,
            optimization=optimization,
            validation=validation,
            scorecard=scorecard,
            candidate=candidate,
            manifest={},
            events=events,
        )
        result.manifest = reproducibility_manifest(result)
        self.registry.save("scorecards", scorecard.scorecard_id, scorecard.model_dump(mode="json"))
        self.registry.save("candidates", candidate.candidate_id, candidate.model_dump(mode="json"))
        for event in events:
            self.registry.save("events", event.event_id, event.model_dump(mode="json"))
        return result

    def approve_candidate(self, candidate_id: str, *, approved_by: str, notes: str | None = None) -> ResearchCandidate:
        row = self.registry.get("candidates", candidate_id)
        if not row:
            raise ValueError("candidate not found")
        candidate = ResearchCandidate.model_validate(row)
        if candidate.stale:
            candidate = candidate.model_copy(update={"promotion_status": CandidateStatus.EXPIRED, "rejection_reasons": [*candidate.rejection_reasons, "candidate is stale"]})
        elif candidate.promotion_status == CandidateStatus.QUALIFIED:
            candidate = candidate.model_copy(update={"promotion_status": CandidateStatus.APPROVED, "approved_by": approved_by, "approved_at": utc_now(), "approval_notes": notes})
        else:
            candidate = candidate.model_copy(update={"rejection_reasons": [*candidate.rejection_reasons, "only qualified candidates can be approved"]})
        self.registry.save("candidates", candidate.candidate_id, candidate.model_dump(mode="json"))
        return candidate

    def reject_candidate(self, candidate_id: str, reason: str) -> ResearchCandidate:
        row = self.registry.get("candidates", candidate_id)
        if not row:
            raise ValueError("candidate not found")
        candidate = ResearchCandidate.model_validate(row).model_copy(update={"promotion_status": CandidateStatus.REJECTED})
        candidate.rejection_reasons.append(reason)
        self.registry.save("candidates", candidate.candidate_id, candidate.model_dump(mode="json"))
        return candidate


_SERVICE: ResearchService | None = None


def get_research_service() -> ResearchService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ResearchService()
    return _SERVICE

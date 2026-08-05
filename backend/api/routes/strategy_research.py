from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.research.models import (
    BacktestConfiguration,
    DatasetSnapshot,
    ParameterSpace,
    ResearchExperiment,
    WalkForwardPlan,
    WorkflowRequest,
    WorkflowResult,
)
from backend.research.services import get_research_service

router = APIRouter(prefix="/api/research/strategy", tags=["strategy-research"])


class ExperimentCreateRequest(BaseModel):
    name: str
    objective: str
    hypothesis: str
    strategy_id: str
    strategy_version: str = "1.0.0"
    dataset_snapshot_id: str
    parameter_space: ParameterSpace = ParameterSpace()


class BacktestCreateRequest(BaseModel):
    strategy_id: str
    dataset: DatasetSnapshot
    configuration: BacktestConfiguration = BacktestConfiguration()
    parameter_set: dict[str, Any] = {}


class OptimizationCreateRequest(BaseModel):
    strategy_id: str
    dataset: DatasetSnapshot
    configuration: BacktestConfiguration = BacktestConfiguration()
    parameter_space: ParameterSpace = ParameterSpace()
    method: str = "grid"
    objective: str = "sharpe_ratio"
    max_trials: int = 100
    random_seed: int = 42


class ValidationCreateRequest(BaseModel):
    strategy_id: str
    dataset: DatasetSnapshot
    configuration: BacktestConfiguration = BacktestConfiguration()
    plan: WalkForwardPlan = WalkForwardPlan()
    selected_parameters: dict[str, Any] = {}


class CandidateDecisionRequest(BaseModel):
    user: str = "researcher"
    reason: str | None = None
    notes: str | None = None


@router.post("/experiments", response_model=ResearchExperiment)
def create_experiment(request: ExperimentCreateRequest) -> ResearchExperiment:
    return get_research_service().create_experiment(**request.model_dump())


@router.get("/experiments", response_model=list[ResearchExperiment])
def list_experiments() -> list[ResearchExperiment]:
    return get_research_service().list_experiments()


@router.get("/experiments/{experiment_id}", response_model=ResearchExperiment)
def get_experiment(experiment_id: str) -> ResearchExperiment:
    experiment = get_research_service().get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return experiment


@router.post("/backtests")
def create_backtest(request: BacktestCreateRequest) -> dict[str, Any]:
    try:
        run = get_research_service().run_backtest(request.strategy_id, request.dataset, request.configuration, request.parameter_set)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.model_dump(mode="json")


@router.post("/optimizations")
def create_optimization(request: OptimizationCreateRequest) -> dict[str, Any]:
    try:
        job = get_research_service().run_optimization(request.strategy_id, request.dataset, request.configuration, request.parameter_space, method=request.method, objective=request.objective, max_trials=request.max_trials, random_seed=request.random_seed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return job.model_dump(mode="json")


@router.post("/validations")
def create_validation(request: ValidationCreateRequest) -> dict[str, Any]:
    try:
        job = get_research_service().run_validation(request.strategy_id, request.dataset, request.configuration, request.plan, selected_parameters=request.selected_parameters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return job.model_dump(mode="json")


@router.post("/workflow", response_model=WorkflowResult)
def run_workflow(request: WorkflowRequest) -> WorkflowResult:
    try:
        return get_research_service().run_workflow(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/scorecards/{scorecard_id}")
def get_scorecard(scorecard_id: str) -> dict[str, Any]:
    row = get_research_service().registry.get("scorecards", scorecard_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scorecard not found")
    return row


@router.get("/candidates")
def list_candidates(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = get_research_service().registry.list("candidates")
    return {"items": rows[:limit], "total": len(rows)}


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str) -> dict[str, Any]:
    row = get_research_service().registry.get("candidates", candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return row


@router.post("/candidates/{candidate_id}/approve")
def approve_candidate(candidate_id: str, request: CandidateDecisionRequest) -> dict[str, Any]:
    try:
        return get_research_service().approve_candidate(candidate_id, approved_by=request.user, notes=request.notes).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: str, request: CandidateDecisionRequest) -> dict[str, Any]:
    try:
        return get_research_service().reject_candidate(candidate_id, request.reason or "Rejected by reviewer").model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    rows = get_research_service().registry.list("backtests") + get_research_service().registry.list("optimizations") + get_research_service().registry.list("validations")
    for row in rows:
        for artifact in row.get("artifacts", []):
            if artifact.get("artifact_id") == artifact_id:
                return artifact
    raise HTTPException(status_code=404, detail="artifact not found")


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    for bucket in ("backtests", "optimizations", "validations"):
        row = get_research_service().registry.get(bucket, job_id)
        if row:
            return row
    raise HTTPException(status_code=404, detail="job not found")

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.market_data.models import AssetClass, DataQualityMetadata
from backend.strategies.engine import StrategyEngine
from backend.strategies.models import StrategyEvaluation, StrategyRegistration, StrategySpec
from backend.strategies.registry import get_strategy, list_strategy_registrations
from backend.strategies.serialization import decision_to_summary, proposal_to_overlay

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

_ENGINE = StrategyEngine()
_EVALUATIONS: dict[str, StrategyEvaluation] = {}


class StrategyEvaluateRequest(BaseModel):
    strategy_id: str | None = None
    spec: StrategySpec | None = None
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    timeframe: str | None = None
    bars: list[dict[str, Any]] = Field(default_factory=list)
    dataset_snapshot_id: str | None = None
    data_quality: DataQualityMetadata | None = None


class StrategyValidateRequest(BaseModel):
    spec: StrategySpec


@router.get("", response_model=list[StrategyRegistration])
def list_strategies() -> list[StrategyRegistration]:
    return list_strategy_registrations()


@router.get("/version")
def strategy_engine_version() -> dict[str, str]:
    return {"engine": _ENGINE.config.engine_name, "version": _ENGINE.config.version}


@router.post("/validate")
def validate_strategy(request: StrategyValidateRequest) -> dict[str, Any]:
    try:
        spec = _ENGINE.compile(request.spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"valid": True, "strategy_id": spec.strategy.id, "strategy_hash": spec.strategy_hash()}


@router.post("/compile")
def compile_strategy(request: StrategyValidateRequest) -> dict[str, Any]:
    return validate_strategy(request)


@router.post("/evaluate", response_model=StrategyEvaluation)
def evaluate_strategy(request: StrategyEvaluateRequest) -> StrategyEvaluation:
    spec = request.spec or (get_strategy(request.strategy_id) if request.strategy_id else None)
    if spec is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if request.timeframe:
        spec = spec.model_copy(update={"timeframes": spec.timeframes.model_copy(update={"execution": request.timeframe})})
    try:
        evaluation = _ENGINE.evaluate_bars(
            spec,
            request.bars,
            symbol=request.symbol,
            instrument_id=request.instrument_id,
            asset_class=request.asset_class,
            dataset_snapshot_id=request.dataset_snapshot_id,
            data_quality=request.data_quality,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _EVALUATIONS[evaluation.evaluation_id] = evaluation
    return evaluation


@router.get("/evaluations/{evaluation_id}", response_model=StrategyEvaluation)
def get_evaluation(evaluation_id: str) -> StrategyEvaluation:
    evaluation = _EVALUATIONS.get(evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return evaluation


@router.get("/evaluations/{evaluation_id}/inspector")
def get_evaluation_inspector(evaluation_id: str) -> dict[str, Any]:
    evaluation = get_evaluation(evaluation_id)
    return {
        "evaluation_id": evaluation.evaluation_id,
        "decisions": [decision_to_summary(decision) for decision in evaluation.decisions],
        "overlays": [proposal_to_overlay(proposal) for proposal in evaluation.proposals],
        "warnings": evaluation.warnings,
        "events": [event.model_dump(mode="json") for event in evaluation.events],
    }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, Any]:
    for evaluation in _EVALUATIONS.values():
        for proposal in evaluation.proposals:
            if proposal.proposal_id == proposal_id:
                return proposal.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="proposal not found")


@router.get("/{strategy_id}", response_model=StrategySpec)
def get_strategy_spec(strategy_id: str) -> StrategySpec:
    spec = get_strategy(strategy_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return spec

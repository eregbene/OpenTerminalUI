from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.adaptive_management.decision_explainer import explain_entry_decision, explain_management_action
from backend.adaptive_management.service import adaptive_management_service
from backend.auth.deps import get_current_user
from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.models.user import User

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/probabilities")
async def probabilities(dimensions: str | None = None, session_id: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    dims = [row.strip() for row in dimensions.split(",") if row.strip()] if dimensions else None
    return adaptive_management_service.probability_report(dimensions=dims, session_id=session_id)


@router.get("/entry-score")
async def entry_score(symbol: str = "EURUSD", current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.entry_quality_score(symbol)


@router.get("/management-score")
async def management_score(position_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.management_quality_verdict(position_id)


@router.get("/replay")
async def replay(position_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.replay_results(position_id)


@router.get("/strategy-performance")
async def strategy_performance(strategy_id: str | None = None, session_id: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    report = adaptive_management_service.probability_report(dimensions=["strategy_id"], session_id=session_id)
    if not strategy_id:
        return report
    matching = {key: stats for key, stats in report["cohorts"].items() if key == f"strategy_id={strategy_id}"}
    return {**report, "cohorts": matching}


@router.get("/learning-summary")
async def learning_summary(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return adaptive_management_service.learning_summary()


@router.get("/explain-entry")
async def explain_entry(symbol: str = "EURUSD", current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Part 8 Decision Explainer for an entry candidate -- renders the SAME entry_quality_score
    used by /entry-score into plain accepted/rejected reasoning."""
    score = await mt5_autonomous_service.entry_quality_score(symbol)
    return explain_entry_decision(score)


@router.get("/explain-action")
async def explain_action(action_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Part 8 Decision Explainer for a single management action -- looks the action up among
    recently recorded actions and renders its reason/evidence/considered-alternatives."""
    action = next((row for row in adaptive_management_service.actions() if row.get("action_id") == action_id), None)
    if action is None:
        return {"status": "not_found", "action_id": action_id}
    return explain_management_action(action)

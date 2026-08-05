from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.auth.deps import get_current_user
from backend.models.user import User
from backend.portfolio_execution.service import correlation_engine, execution_manager, portfolio_manager

router = APIRouter(tags=["portfolio-execution"])


@router.get("/api/portfolio/status")
async def portfolio_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return portfolio_manager.status()


@router.get("/api/portfolio/exposure")
async def portfolio_exposure(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return portfolio_manager.exposure()


@router.get("/api/portfolio/risk")
async def portfolio_risk(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return portfolio_manager.risk()


@router.get("/api/portfolio/correlation")
async def portfolio_correlation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    latest = portfolio_manager.latest_snapshot() or {}
    return latest.get("correlation_matrix") or await correlation_engine.matrix([])


@router.get("/api/portfolio/execution")
async def portfolio_execution(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return execution_manager.status()


@router.get("/api/portfolio/journal")
async def portfolio_journal(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return execution_manager.journal()


@router.get("/api/execution/status")
async def execution_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return execution_manager.status()


@router.get("/api/execution/orders")
async def execution_orders(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": execution_manager.orders()}


@router.get("/api/execution/metrics")
async def execution_metrics(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return execution_manager.metrics()

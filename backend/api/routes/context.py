from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth.deps import get_current_user
from backend.decision_context.service import decision_context_service
from backend.models.user import User

router = APIRouter(prefix="/api/context", tags=["decision-context"])


class RefreshRequest(BaseModel):
    jobs: list[str] | None = None


@router.get("/providers/health")
async def context_provider_health(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await decision_context_service.provider_health()


@router.get("/forex/{symbol}")
async def context_forex(symbol: str, timestamp: datetime | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await decision_context_service.forex_context(symbol, timestamp)


@router.get("/risk/{symbol}")
async def context_risk(symbol: str, timestamp: datetime | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await decision_context_service.context_risk(symbol, timestamp)


@router.get("/calendar")
async def context_calendar(symbol: str | None = None, currency: str | None = None, limit: int = Query(default=100, ge=1, le=500), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await decision_context_service.calendar(symbol=symbol, currency=currency, limit=limit)


@router.get("/historical")
async def context_historical(symbol: str, timestamp: datetime, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await decision_context_service.historical(symbol, timestamp)


@router.post("/refresh")
async def context_refresh(payload: RefreshRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    result = await decision_context_service.refresh(payload.jobs)
    if any(row.get("status") == "duplicate_active_job_rejected" for row in result.get("results", {}).values()):
        raise HTTPException(status_code=409, detail=result)
    return result

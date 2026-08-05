from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from backend.auth.deps import get_current_user
from backend.economic_intelligence.persistence import get_event, query_events, query_news
from backend.economic_intelligence.schemas import BackfillRequest, DryRunEvaluateRequest, EvaluateRequest, ShadowReplayRequest
from backend.economic_intelligence.service import economic_intelligence_service
from backend.models.user import User

router = APIRouter(prefix="/api/economic-intelligence", tags=["economic-intelligence"])


@router.get("/health")
async def health(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.provider_health_report()


@router.get("/events")
async def events(currency: str | None = None, limit: int = 200, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_events(currencies=[currency] if currency else None, limit=limit)}


@router.get("/events/upcoming")
async def upcoming_events(hours: int = 48, currency: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {"items": query_events(currencies=[currency] if currency else None, start=now, end=now + timedelta(hours=max(1, min(24 * 30, hours))), limit=200)}


@router.get("/events/{event_id}")
async def event_detail(event_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = get_event(event_id)
    return row or {"status": "not_found", "event_id": event_id}


@router.post("/events/{event_id}/refresh-detail")
async def refresh_event_detail(event_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.refresh_event_detail_for_event_id(event_id)


@router.get("/news")
async def news(currency: str | None = None, limit: int = 100, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_news(currencies=[currency] if currency else None, limit=limit)}


@router.get("/context/{symbol}")
async def context(symbol: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.context_for_symbol(symbol)


@router.post("/sync/calendar")
async def sync_calendar(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.refresh(["ff_calendar_refresh"])


@router.post("/sync/news")
async def sync_news(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.refresh(["ff_news_refresh"])


@router.post("/backfill")
async def backfill(payload: BackfillRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.backfill(payload.start_month, payload.end_month, resume_from=payload.resume_from)


@router.post("/evaluate")
async def evaluate(payload: EvaluateRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.evaluate_entry(canonical_pair=payload.symbol, direction=payload.direction)


@router.get("/events/{event_id}/timeline")
async def event_timeline(event_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.event_timeline(event_id)


@router.get("/shadow/decisions")
async def shadow_decisions(symbol: str | None = None, mode: str | None = None, limit: int = 200, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.list_shadow_decisions(symbol=symbol, mode=mode, limit=limit)


@router.get("/shadow/summary")
async def shadow_summary(symbol: str | None = None, mode: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.shadow_summary(symbol=symbol, mode=mode)


@router.post("/shadow/replay")
async def shadow_replay(payload: ShadowReplayRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.shadow_replay(payload.snapshot_id)


@router.post("/evaluate/dry-run")
async def evaluate_dry_run(payload: DryRunEvaluateRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await economic_intelligence_service.dry_run_evaluate(payload.model_dump())

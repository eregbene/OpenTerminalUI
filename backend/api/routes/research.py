from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.research import service
from backend.research.performance_intelligence import strategy_performance_service

router = APIRouter(prefix="/api/research", tags=["research"])


class IngestRequest(BaseModel):
    query: str = "cat:q-fin.*"
    max_results: int = Field(25, ge=1, le=100)
    with_fulltext: bool = True


@router.post("/ingest")
async def ingest(payload: IngestRequest) -> dict[str, Any]:
    return await service.ingest_arxiv(
        payload.query,
        max_results=payload.max_results,
        with_fulltext=payload.with_fulltext,
    )


class IngestUrlRequest(BaseModel):
    url: str


@router.post("/ingest_url")
async def ingest_url(payload: IngestUrlRequest) -> dict[str, Any]:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    return await service.ingest_url(url)


@router.get("/search")
async def search(q: str, k: int = 10) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q is required")
    k = max(1, min(50, k))
    return {"query": q, "results": service.search(q, k=k)}


@router.get("/items")
async def items(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(200, limit))
    return {"items": service.list_items(limit=limit)}


@router.get("/strategies")
async def research_strategies() -> dict[str, Any]:
    return {"items": strategy_performance_service.strategies()}


@router.get("/strategies/{strategy_id}")
async def research_strategy(strategy_id: str) -> dict[str, Any]:
    row = strategy_performance_service.strategy(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return row


@router.get("/leaderboard")
async def research_leaderboard() -> dict[str, Any]:
    return strategy_performance_service.leaderboard()


@router.get("/performance")
async def research_performance() -> dict[str, Any]:
    return strategy_performance_service.performance()


@router.get("/equity")
async def research_equity() -> dict[str, Any]:
    return strategy_performance_service.equity()


@router.get("/calibration")
async def research_calibration() -> dict[str, Any]:
    return strategy_performance_service.calibration()


@router.post("/performance/refresh")
async def research_performance_refresh() -> dict[str, Any]:
    return strategy_performance_service.refresh_from_paper_trades()


@router.get("/performance/analysis")
async def research_performance_analysis() -> dict[str, Any]:
    return strategy_performance_service.first_analysis()

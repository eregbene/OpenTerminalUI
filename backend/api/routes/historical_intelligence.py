"""Read-only observability for Historical Market Intelligence (Part 22). No mutation endpoints
here -- ingestion/replay/parity runs are triggered by scripts/scheduled jobs (later phases), not
by an API caller, matching "do not backtest during every M5 cycle" and the OFF-by-default mode
gate (backend.historical_intelligence.modes)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.auth.deps import get_current_user
from backend.historical_intelligence.modes import current_mode
from backend.historical_intelligence.orm import (
    HistoricalIngestionRunORM,
    HistoricalProviderReconciliationORM,
    HistoricalReplayParityCheckORM,
    HistoricalReplayRunORM,
)
from backend.models.user import User
from backend.shared.db import SessionLocal

router = APIRouter(prefix="/api/historical-intelligence", tags=["historical-intelligence"])


def _orm_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.get("/mode")
async def mode(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"mode": current_mode().value}


@router.get("/ingestion-runs")
async def ingestion_runs(limit: int = Query(50, le=200), canonical_symbol: str | None = None, provider: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    with SessionLocal() as db:
        query = db.query(HistoricalIngestionRunORM)
        if canonical_symbol:
            query = query.filter(HistoricalIngestionRunORM.canonical_symbol == canonical_symbol.upper())
        if provider:
            query = query.filter(HistoricalIngestionRunORM.provider == provider.upper())
        rows = query.order_by(HistoricalIngestionRunORM.started_at.desc()).limit(limit).all()
        return {"items": [_orm_dict(row) for row in rows]}


@router.get("/provider-reconciliations")
async def provider_reconciliations(limit: int = Query(50, le=200), canonical_symbol: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    with SessionLocal() as db:
        query = db.query(HistoricalProviderReconciliationORM)
        if canonical_symbol:
            query = query.filter(HistoricalProviderReconciliationORM.canonical_symbol == canonical_symbol.upper())
        rows = query.order_by(HistoricalProviderReconciliationORM.created_at.desc()).limit(limit).all()
        return {"items": [_orm_dict(row) for row in rows]}


@router.get("/replay-runs")
async def replay_runs(limit: int = Query(50, le=200), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    with SessionLocal() as db:
        rows = db.query(HistoricalReplayRunORM).order_by(HistoricalReplayRunORM.started_at.desc()).limit(limit).all()
        return {"items": [_orm_dict(row) for row in rows]}


@router.get("/parity-checks")
async def parity_checks(limit: int = Query(100, le=500), verdict: str | None = None, canonical_symbol: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    with SessionLocal() as db:
        query = db.query(HistoricalReplayParityCheckORM)
        if verdict:
            query = query.filter(HistoricalReplayParityCheckORM.verdict == verdict.upper())
        if canonical_symbol:
            query = query.filter(HistoricalReplayParityCheckORM.canonical_symbol == canonical_symbol.upper())
        rows = query.order_by(HistoricalReplayParityCheckORM.created_at.desc()).limit(limit).all()
        items = [_orm_dict(row) for row in rows]
    verdict_counts: dict[str, int] = {}
    for item in items:
        verdict_counts[item["verdict"]] = verdict_counts.get(item["verdict"], 0) + 1
    return {"items": items, "verdict_counts": verdict_counts}

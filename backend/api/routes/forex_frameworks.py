from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.auth.deps import get_current_user
from backend.forex_frameworks.registry import registry
from backend.forex_frameworks.service import context_from_snapshot, framework_service
from backend.forex_intelligence.instruments import SUPPORTED_TIMEFRAMES, normalize_forex_symbol
from backend.forex_intelligence.service import ForexIntelligenceService
from backend.models.user import User
from backend.services.forex_service import service as forex_service

router = APIRouter(prefix="/api/forex-frameworks", tags=["forex-frameworks"])
intelligence_service = ForexIntelligenceService()


class FrameworkAnalyzeRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "1h"
    range: str = "3mo"
    framework_ids: list[str] | None = Field(default=None)
    persist: bool = True


@router.get("")
async def frameworks(_: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"frameworks": [definition.model_dump(mode="json") for definition in registry.definitions()]}


@router.get("/{framework_id}")
async def framework(framework_id: str, _: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return registry.definition(registry.require(framework_id)).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _run_analysis(req: FrameworkAnalyzeRequest, db: Session) -> dict[str, Any]:
    symbol = normalize_forex_symbol(req.symbol)
    if req.timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported forex timeframe: {req.timeframe}")
    if req.framework_ids:
        for framework_id in req.framework_ids:
            registry.require(framework_id)
    chart = await forex_service.get_pair_chart(symbol, interval=req.timeframe, range_str=req.range)
    snapshot = intelligence_service.analyze_rows(
        chart.get("candles") or [],
        symbol=symbol,
        timeframe=req.timeframe,
        db=db,
        source_provider="yahoo" if str(chart.get("source_symbol", "")).endswith(("=X", "=F")) or chart.get("source_symbol") == "GC=F" else "fallback",
        source_dataset_id=f"forex-frameworks:{symbol}:{req.timeframe}:{req.range}:{chart.get('source_symbol') or 'unknown'}",
        provider_symbol_value=chart.get("source_symbol"),
    ).model_dump(mode="json")
    ctx = context_from_snapshot(snapshot, chart.get("candles") or [])
    signals = framework_service.analyze(ctx, req.framework_ids)
    if req.persist:
        framework_service.persist(db, ctx, signals)
    comparison = framework_service.compare(signals)
    thesis = framework_service.thesis(comparison)
    return {
        "symbol": symbol,
        "timeframe": req.timeframe,
        "provider": snapshot.get("provider"),
        "provider_symbol": snapshot.get("provider_symbol"),
        "provider_metadata": ctx.provider_metadata,
        "feature_vector_id": snapshot.get("feature_vector_id"),
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "comparison": comparison.model_dump(mode="json", exclude={"signals"}),
        "thesis": thesis.model_dump(mode="json"),
        "read_only": True,
    }


@router.post("/analyze")
async def analyze(req: FrameworkAnalyzeRequest, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return await _run_analysis(req, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"framework analysis unavailable: {exc}") from exc


@router.get("/{symbol}/latest")
async def latest(symbol: str, timeframe: str = "1h", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = framework_service.latest(db, symbol=symbol, timeframe=timeframe)
    if not rows:
        raise HTTPException(status_code=404, detail="no framework signals found")
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "signals": rows}


@router.get("/{symbol}/history")
async def history(symbol: str, timeframe: str = "1h", limit: int = Query(default=250, ge=1, le=1000), _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "signals": framework_service.history(db, symbol=symbol, timeframe=timeframe, limit=limit)}


@router.get("/{symbol}/{framework_id}/latest")
async def framework_latest(symbol: str, framework_id: str, timeframe: str = "1h", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    registry.require(framework_id)
    rows = framework_service.latest(db, symbol=symbol, timeframe=timeframe, framework_id=framework_id)
    if not rows:
        raise HTTPException(status_code=404, detail="no framework signal found")
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "framework_id": framework_id, "signal": rows[0]}


@router.get("/{symbol}/{framework_id}/history")
async def framework_history(symbol: str, framework_id: str, timeframe: str = "1h", limit: int = Query(default=250, ge=1, le=1000), _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    registry.require(framework_id)
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "framework_id": framework_id, "signals": framework_service.history(db, symbol=symbol, timeframe=timeframe, framework_id=framework_id, limit=limit)}


@router.get("/{symbol}/comparison")
async def comparison(symbol: str, timeframe: str = "1h", range: str = "3mo", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = await _run_analysis(FrameworkAnalyzeRequest(symbol=symbol, timeframe=timeframe, range=range, persist=True), db)
    return payload["comparison"]


@router.get("/{symbol}/thesis")
async def thesis(symbol: str, timeframe: str = "1h", range: str = "3mo", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = await _run_analysis(FrameworkAnalyzeRequest(symbol=symbol, timeframe=timeframe, range=range, persist=True), db)
    return payload["thesis"]

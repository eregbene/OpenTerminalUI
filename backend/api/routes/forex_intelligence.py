from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.auth.deps import get_current_user
from backend.forex_intelligence.feature_store import FEATURE_SCHEMA_VERSION
from backend.forex_intelligence.instruments import SUPPORTED_TIMEFRAMES, normalize_forex_symbol
from backend.forex_intelligence.orm import ForexFeatureVectorORM
from backend.forex_intelligence.service import ForexIntelligenceService
from backend.models.user import User
from backend.services.forex_service import service as forex_service

router = APIRouter(prefix="/api/forex-intelligence", tags=["forex-intelligence"])

service = ForexIntelligenceService()


class AnalyzeRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "1h"
    source_provider: str = "provided"
    source_dataset_id: str | None = None
    bars: list[dict[str, Any]] = Field(default_factory=list, min_length=30)


def _serialize_feature(row: ForexFeatureVectorORM) -> dict[str, Any]:
    return {
        "feature_vector_id": row.feature_vector_id,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "candle_timestamp": row.candle_timestamp,
        "candle_content_hash": row.candle_content_hash,
        "feature_version": row.feature_version,
        "engine_version": row.engine_version,
        "source_provider": row.source_provider,
        "source_dataset_id": row.source_dataset_id,
        "source_candle_id": row.source_candle_id,
        "generated_at": row.generated_at,
        "completeness": row.completeness,
        "quality_status": row.quality_status,
        "feature": row.feature_payload,
        "structure": row.structure_payload,
        "liquidity": row.liquidity_payload,
        "volatility": row.volatility_payload,
        "trend": row.trend_payload,
        "momentum": row.momentum_payload,
        "session": row.session_payload,
        "regime": row.regime_payload,
        "confluence": row.confluence_payload,
        "explanation": row.explanation_payload,
    }


async def _analyze_symbol(symbol: str, timeframe: str, range: str, db: Session) -> dict[str, Any]:
    normalized = normalize_forex_symbol(symbol)
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported forex timeframe: {timeframe}")
    payload = await forex_service.get_pair_chart(normalized, interval=timeframe, range_str=range)
    provider_symbol = str(payload.get("source_symbol") or "")
    source_provider = "yahoo" if provider_symbol.endswith("=X") or provider_symbol in {"JPY=X", "CHF=X", "CAD=X"} else "fallback"
    source_dataset_id = f"forex:{normalized}:{timeframe}:{range}:{provider_symbol or 'unknown'}"
    return service.analyze_rows(
        payload.get("candles") or [],
        symbol=normalized,
        timeframe=timeframe,
        db=db,
        source_provider=source_provider,
        source_dataset_id=source_dataset_id,
        provider_symbol_value=provider_symbol or None,
    ).model_dump(mode="json")


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return service.analyze_rows(
            req.bars,
            symbol=req.symbol,
            timeframe=req.timeframe,
            db=db,
            source_provider=req.source_provider,
            source_dataset_id=req.source_dataset_id,
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/eurusd")
async def eurusd(interval: str = "1h", range: str = "3mo", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return await _analyze_symbol("EURUSD", interval, range, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"forex intelligence unavailable: {exc}") from exc


@router.get("/features/{feature_vector_id}")
async def feature_by_id(feature_vector_id: str, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = service.get_feature_vector(db, feature_vector_id)
    if row is None:
        raise HTTPException(status_code=404, detail="feature vector not found")
    return _serialize_feature(row)


@router.get("/{symbol}")
async def analyze_symbol(symbol: str, timeframe: str = "1h", range: str = "3mo", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return await _analyze_symbol(symbol, timeframe, range, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"forex intelligence unavailable: {exc}") from exc


@router.get("/{symbol}/latest")
async def latest(symbol: str, timeframe: str = "1h", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = service.latest(db, symbol=symbol, timeframe=timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="no feature vector found for symbol/timeframe")
    return _serialize_feature(row)


@router.get("/{symbol}/history")
async def history(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(default=250, ge=1, le=1000),
    feature_version: str | None = Query(default=FEATURE_SCHEMA_VERSION),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = service.history(db, symbol=symbol, timeframe=timeframe, limit=limit, feature_version=feature_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "feature_version": feature_version, "rows": [_serialize_feature(row) for row in rows]}


@router.get("/{symbol}/features")
async def features(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(default=250, ge=1, le=1000),
    feature_version: str | None = Query(default=FEATURE_SCHEMA_VERSION),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = service.history(db, symbol=symbol, timeframe=timeframe, limit=limit, feature_version=feature_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_forex_symbol(symbol), "timeframe": timeframe, "rows": [row.feature_payload for row in rows]}


@router.get("/{symbol}/overlays")
async def overlays(symbol: str, timeframe: str = "1h", range: str = "3mo", _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        snapshot = await _analyze_symbol(symbol, timeframe, range, db)
        return {
            "symbol": snapshot["symbol"],
            "timeframe": snapshot["timeframe"],
            "feature_vector_id": snapshot.get("feature_vector_id"),
            "overlays": snapshot.get("overlays", []),
            "warnings": snapshot.get("warnings", []),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"forex overlays unavailable: {exc}") from exc

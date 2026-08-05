from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.deps import get_current_user
from backend.models.user import User
from backend.intelligence.trading.diagnostics import get_replay, latest_diagnostics, replay_history, run_replay
from backend.intelligence.trading.runtime import ai_trading_service, auto_paper_service

router = APIRouter(prefix="/api/ai-trading", tags=["ai-trading"])


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "15m"


class CycleRequest(BaseModel):
    execute: bool = False


class ReplayRequest(BaseModel):
    symbol: str = "EURUSD"
    days: int = 7


@router.get("/status")
async def ai_trading_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    status = await ai_trading_service.status()
    status["usage"] = auto_paper_service.usage()
    status["scheduler"] = auto_paper_service.scheduler_status()
    status["emergency_disabled"] = auto_paper_service.state.emergency_disabled
    return status


@router.post("/shadow/analyze")
async def shadow_analyze(payload: AnalyzeRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return (await ai_trading_service.analyze(payload.symbol, payload.timeframe)).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SYMBOL", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "AI_SHADOW_SAFETY_BLOCK", "message": str(exc)}) from exc


@router.get("/decisions")
async def decisions(limit: int = 50, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": ai_trading_service.decisions(limit)}


@router.get("/decisions/{decision_id}")
async def decision(decision_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = ai_trading_service.decision(decision_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "decision not found"})
    return row


@router.get("/shadow/performance")
async def performance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return ai_trading_service.performance()


@router.get("/institutional/status")
async def institutional_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return ai_trading_service.institutional_status()


@router.get("/usage")
async def usage(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return auto_paper_service.usage()


@router.get("/scheduler/status")
async def scheduler_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return auto_paper_service.scheduler_api_status()


@router.get("/diagnostics/latest")
async def diagnostics_latest(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return latest_diagnostics()


@router.get("/data-quality/latest")
async def data_quality_latest(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    latest = latest_diagnostics() or {}
    quality = latest.get("data_quality") or {}
    if quality:
        return {"source": "latest_diagnostics", "data_quality": quality}
    history = replay_history(1)
    if history:
        return {"source": "latest_replay", "data_quality": (history[0].get("report") or {}).get("data_quality") or {}, "canonical_data": (history[0].get("report") or {}).get("canonical_data") or {}}
    return {"source": "none", "data_quality": {"status": "UNKNOWN", "reasons": ["NO_DIAGNOSTICS_AVAILABLE"]}}


@router.get("/data-quality/history")
async def data_quality_history(limit: int = 25, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    items = []
    for row in replay_history(limit):
        report = row.get("report") or {}
        items.append({"job_id": row.get("job_id"), "created_at": row.get("created_at"), "symbol": row.get("symbol"), "data_quality": report.get("data_quality"), "canonical_data": report.get("canonical_data")})
    return {"items": items}


@router.get("/strategy-health")
async def strategy_health(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    history = replay_history(1)
    report = (history[0].get("report") if history else None) or {}
    analytics = report.get("analytics") or {}
    signals = analytics.get("strategy_signals_by_strategy") or {}
    eligible = analytics.get("eligible_signals_by_strategy") or {}
    never = set(analytics.get("strategies_never_eligible") or [])
    rejection_counts = analytics.get("rejection_count_by_reason") or {}
    items = []
    for name in sorted(set(signals) | set(eligible) | never):
        status = "HEALTHY" if int(eligible.get(name) or 0) > 0 else "NO_RECENT_ELIGIBILITY"
        if name in never and rejection_counts.get("REQUIRED_DATA_MISSING"):
            status = "INPUT_UNAVAILABLE"
        elif name in never and rejection_counts.get("EMA200_WARMUP_UNAVAILABLE"):
            status = "NEEDS_MORE_DATA"
        items.append({"strategy": name, "signals": int(signals.get(name) or 0), "eligible": int(eligible.get(name) or 0), "status": status})
    return {"source": history[0].get("job_id") if history else None, "items": items, "rejection_count_by_reason": rejection_counts}


@router.post("/replay")
async def replay(payload: ReplayRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    days = max(1, min(payload.days, 30))
    return await run_replay(symbol=payload.symbol, days=days, config=ai_trading_service.config)


@router.get("/replay/{job_id}")
async def replay_job(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = get_replay(job_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "replay job not found"})
    return row


@router.get("/replay/{job_id}/report")
async def replay_report(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = get_replay(job_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "replay job not found"})
    return row.get("report") or {}


@router.get("/replay/{job_id}/comparison")
async def replay_comparison(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = get_replay(job_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "replay job not found"})
    return row.get("comparison") or {"baseline": "none", "read_only": True, "metrics": {}}


@router.post("/emergency-disable")
async def emergency_disable(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await auto_paper_service.emergency_disable()


@router.post("/emergency-enable")
async def emergency_enable(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await auto_paper_service.emergency_enable()


@router.post("/scheduler/run-once")
async def run_once(payload: CycleRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await auto_paper_service.run_cycle(execute=payload.execute, owner="api")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "AI_AUTO_PAPER_BLOCKED", "message": str(exc)}) from exc

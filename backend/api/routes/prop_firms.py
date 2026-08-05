from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.deps import get_current_user
from backend.models.user import User
from backend.research.prop_firms import prop_firm_lab

router = APIRouter(prefix="/api/research/prop-firms", tags=["research-prop-firms"])


@router.get("/profiles")
def profiles(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": prop_firm_lab.profiles()}


@router.post("/profiles")
def upsert_profile(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return prop_firm_lab.upsert_profile(payload)


@router.get("/profiles/{profile_id}")
def profile(profile_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = prop_firm_lab.profile(profile_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "profile not found"})
    return row


@router.post("/simulations")
async def create_simulation(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await prop_firm_lab.create_simulation(payload)


@router.get("/simulations")
def simulations(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": prop_firm_lab.simulations()}


@router.get("/simulations/{job_id}")
def simulation(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = prop_firm_lab.simulation(job_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "simulation not found"})
    return row


@router.post("/simulations/{job_id}/cancel")
def cancel_simulation(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = prop_firm_lab.update_job_status(job_id, "CANCELLED")
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "simulation not found"})
    return row


@router.post("/simulations/{job_id}/resume")
def resume_simulation(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = prop_firm_lab.update_job_status(job_id, "RESUMED")
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "simulation not found"})
    return row


@router.get("/simulations/{job_id}/report")
def simulation_report(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (simulation(job_id, current_user).get("report") or {})


@router.get("/simulations/{job_id}/trades")
def simulation_trades(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": simulation(job_id, current_user).get("trades") or []}


@router.get("/simulations/{job_id}/attempts")
def simulation_attempts(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": simulation(job_id, current_user).get("attempts") or []}


@router.get("/simulations/{job_id}/artifacts")
def simulation_artifacts(job_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return simulation(job_id, current_user).get("artifacts") or {}


@router.get("/leaderboard")
def leaderboard(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return prop_firm_lab.leaderboard()


@router.get("/compatibility")
def compatibility(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return prop_firm_lab.compatibility()

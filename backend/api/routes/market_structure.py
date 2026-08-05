from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.deps import get_current_user
from backend.market_data.models import AssetClass
from backend.market_structure import MarketStructureEngine, get_profile
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import MarketStructureConfig, MarketStructureProfile
from backend.market_structure.models import MarketStructureSnapshot
from backend.models.user import User

router = APIRouter(prefix="/api/market-structure", tags=["market-structure"])

_SNAPSHOTS: dict[str, MarketStructureSnapshot] = {}


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "1d"
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    source_dataset_id: str | None = None
    profile: MarketStructureProfile = MarketStructureProfile.BALANCED
    configuration: MarketStructureConfig | None = None
    bars: list[dict[str, Any]] = Field(default_factory=list, min_length=1)
    requested_detectors: list[str] = Field(default_factory=list)
    page_limit: int = Field(default=500, ge=1, le=2000)


class ValidateConfigurationRequest(BaseModel):
    profile: MarketStructureProfile = MarketStructureProfile.BALANCED
    configuration: MarketStructureConfig | None = None


@router.get("/configurations")
async def configurations(_: User = Depends(get_current_user)) -> dict[str, Any]:
    profiles = {}
    for profile in MarketStructureProfile:
        config = get_profile(profile)
        profiles[profile.value] = {
            "configuration": config.stable_payload(),
            "configuration_hash": config.configuration_hash(),
        }
    return {"engine": "bensim-smc", "profiles": profiles}


@router.post("/configurations/validate")
async def validate_configuration(req: ValidateConfigurationRequest, _: User = Depends(get_current_user)) -> dict[str, Any]:
    config = req.configuration or get_profile(req.profile)
    return {
        "valid": True,
        "configuration": config.stable_payload(),
        "configuration_hash": config.configuration_hash(),
        "version": config.version,
    }


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, _: User = Depends(get_current_user)) -> dict[str, Any]:
    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="symbol is required")
    config = req.configuration or get_profile(req.profile)
    try:
        bars = normalize_bars(req.bars, symbol=req.symbol.strip().upper(), timeframe=req.timeframe)
        snapshot = MarketStructureEngine(config).analyze(
            bars,
            symbol=req.symbol.strip().upper(),
            timeframe=req.timeframe,
            instrument_id=req.instrument_id,
            asset_class=req.asset_class,
            source_dataset_id=req.source_dataset_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"analysis failed: {exc}") from exc
    _SNAPSHOTS[snapshot.snapshot_id] = snapshot
    payload = snapshot.model_dump(mode="json")
    for key in (
        "swings",
        "breaks",
        "displacements",
        "liquidity_levels",
        "liquidity_sweeps",
        "imbalances",
        "order_blocks",
        "overlays",
        "events",
        "features",
    ):
        if isinstance(payload.get(key), list):
            payload[key] = payload[key][: req.page_limit]
    return payload


@router.get("/snapshots/{snapshot_id}")
async def snapshot(snapshot_id: str, _: User = Depends(get_current_user)) -> dict[str, Any]:
    item = _SNAPSHOTS.get(snapshot_id)
    if item is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return item.model_dump(mode="json")

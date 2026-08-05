from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.auth.deps import get_current_user
from backend.market_data.registry import get_provider_registry
from backend.market_data.routing import MarketDataRequest, MarketDataRouter
from backend.market_data.capabilities import Capability
from backend.market_data.models import AssetClass
from backend.models.user import User

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/providers")
async def providers(_: User = Depends(get_current_user)) -> dict[str, object]:
    registry = get_provider_registry()
    return {"providers": registry.diagnostics()}


@router.get("/data-health")
async def data_health(_: User = Depends(get_current_user)) -> dict[str, object]:
    registry = get_provider_registry()
    router_ = MarketDataRouter(registry)
    representative = {
        "equity_quote": router_.route(MarketDataRequest(capability=Capability.QUOTE, asset_class=AssetClass.EQUITY)).as_dict(),
        "historical_candles": router_.route(MarketDataRequest(capability=Capability.HISTORICAL_CANDLES, asset_class=AssetClass.EQUITY)).as_dict(),
        "economic_data": router_.route(MarketDataRequest(capability=Capability.ECONOMIC_DATA, asset_class=AssetClass.ECONOMIC)).as_dict(),
        "options_chain": router_.route(MarketDataRequest(capability=Capability.OPTIONS_CHAIN, asset_class=AssetClass.OPTION)).as_dict(),
    }
    return {
        "status": "ok",
        "provider_count": len(registry.all()),
        "routes": representative,
    }

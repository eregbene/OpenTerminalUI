from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes.adaptive_management import router as adaptive_management_router
from backend.api.routes.admin import router as admin_router
from backend.api.routes.analytics import router as analytics_router
from backend.api.routes.api_keys import router as api_keys_router
from backend.api.routes.audit import router as audit_router
from backend.api.routes.brokers import router as brokers_router
from backend.api.routes.context import router as context_router
from backend.api.routes.economic_intelligence import router as economic_intelligence_router
from backend.api.routes.forex_ops import router as forex_ops_router
from backend.api.routes.health import router as health_router
from backend.api.routes.historical_intelligence import router as historical_intelligence_router
from backend.api.routes.intelligence import router as intelligence_router
from backend.api.routes.market_structure import router as market_structure_router
from backend.api.routes.notifications import router as notifications_router
from backend.api.routes.portfolio_execution import router as portfolio_execution_router
from backend.api.routes.public_api import router as public_api_router
from backend.api.routes.quotes import router as quotes_router
from backend.api.routes.search import router as search_router
from backend.api.routes.system_providers import router as system_providers_router
from backend.api.routes.user_layouts import router as user_layouts_router
from backend.api.routes.watchlists import router as watchlists_router
from backend.alerts.routes import router as alerts_router
from backend.equity.routes.auth import router as auth_router
from backend.data_quality.admin_routes import router as admin_data_quality_router

api_router = APIRouter()

# Multi-watchlist routes must register BEFORE any other router that might define a
# conflicting GET /api/watchlists handler.
api_router.include_router(watchlists_router)

# --- Auth & shared infra. These carried an internal "/api/..." prefix and were, before
# Stage 2 of the MT5-only cleanup, only reachable transitively through equity_router
# (backend/equity/routes/__init__.py -> backend/api/routes/{health,admin,audit,api_keys,
# public_api,user_layouts,search,quotes}.py and backend/alerts/routes.py). equity_router
# itself is a non-MT5 (equities) aggregator being unregistered in this stage, so these are
# now registered directly here to keep them alive -- dropping them silently would have taken
# login (auth_router) and the kept Alerts page (alerts_router) down with it. See Stage 2
# report for details.
api_router.include_router(auth_router)
api_router.include_router(health_router, prefix="/api")
api_router.include_router(admin_router, prefix="/api")
api_router.include_router(audit_router, prefix="/api")
api_router.include_router(api_keys_router, prefix="/api")
api_router.include_router(public_api_router, prefix="/api")
api_router.include_router(user_layouts_router, prefix="/api")
api_router.include_router(search_router, prefix="/api")
api_router.include_router(quotes_router, prefix="/api")
api_router.include_router(alerts_router, prefix="/api")

# --- MT5-core routes ---
api_router.include_router(adaptive_management_router)
api_router.include_router(historical_intelligence_router)
api_router.include_router(forex_ops_router)
api_router.include_router(portfolio_execution_router)
api_router.include_router(economic_intelligence_router)
api_router.include_router(brokers_router)
api_router.include_router(context_router)
api_router.include_router(market_structure_router)
api_router.include_router(system_providers_router)
api_router.include_router(intelligence_router)

# These routers already carry their full "/api/..." prefix internally,
# so they must be included WITHOUT an extra prefix (avoids "/api/api/...").
api_router.include_router(analytics_router)
api_router.include_router(notifications_router)
api_router.include_router(admin_data_quality_router)

__all__ = ["api_router"]

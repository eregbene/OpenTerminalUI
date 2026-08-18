from __future__ import annotations

from fastapi import APIRouter

# NOTE (MT5-only cleanup, Stage 2): equity_router itself is no longer registered anywhere
# (backend/api/router.py dropped it -- it was the non-MT5 equities aggregator). This module is
# still imported, though, purely so `backend.equity.routes.auth` (the login/register/refresh
# router, needed by every kept page, MT5 included) remains importable as a submodule of this
# package -- importing any submodule of `backend.equity.routes` runs this __init__.py first.
#
# Everything this __init__.py used to also pull in (backend.api.routes.* flat files like
# admin/health/quotes/search/audit/user_layouts/api_keys/public_api, backend.alerts.routes,
# backend.model_lab, backend.oms, backend.portfolio_lab, backend.reports, backend.screener) has
# been dropped from here: those shared-infra routers are now registered directly in
# backend/api/router.py instead (see the "Auth & shared infra" block there), and the rest
# (model_lab/oms/portfolio_lab/reports/screener) are non-MT5 REMOVE-scope packages that don't
# need to be reachable at all. Trying to keep them wired through this file was actively
# dangerous: backend.screener transitively imports backend.scanner_engine.runner, which itself
# imported backend.api.routes.chart -- a file Stage 2 deleted -- so leaving that import chain in
# place would have broken this module (and therefore login) at process startup.
#
# `equity_router` is now built from only earnings/events/mutual_funds/auth (equities-specific
# except auth) and is not registered by backend/api/router.py -- dead weight, safe to delete
# along with the rest of this package in Stage 3.
from backend.equity.routes import earnings, events, mutual_funds, auth

equity_router = APIRouter()
equity_router.include_router(mutual_funds.router)
equity_router.include_router(events.router)
equity_router.include_router(earnings.router)
equity_router.include_router(auth.router)

__all__ = ["equity_router"]

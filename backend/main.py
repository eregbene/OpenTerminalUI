from __future__ import annotations

from backend.runtime import assert_supported_python

assert_supported_python()

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from backend.api.deps import shutdown_unified_fetcher
from backend.alerts import get_alert_evaluator_service
from backend.auth.middleware import AuthMiddleware
from backend.adapters.registry import get_adapter_registry
from backend.bg_services.instruments_loader import get_instruments_loader
from backend.bg_services.news_ingestor import get_news_ingestor
from backend.bg_services.pcr_snapshot import get_pcr_snapshot_service
from backend.bg_services.scanner_alert_scheduler import get_scanner_alert_scheduler_service
from backend.services.prefetch_worker import get_prefetch_worker
from backend.services.us_tick_stream import get_us_tick_stream_service
from backend.paper_trading import get_paper_engine
from backend.intelligence.trading.runtime import auto_paper_service as ai_auto_paper_service
from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.adaptive_management.service import adaptive_management_service
from backend.economic_intelligence.service import economic_intelligence_service
from backend.portfolio_execution.service import portfolio_manager
from backend.core.service_status import service_status_registry
from backend.config.env import load_local_env
from backend.config.security import validate_runtime_secrets
from backend.config.settings import get_settings
from backend.core.contracts.api import APIErrorEnvelope, APIErrorPayload, BensimAPIError
from backend.core.observability import configure_logging, get_request_id, request_context_middleware
from backend.shared.cache import cache as cache_instance
from backend.shared.db import SessionLocal, init_db
from backend.shared.ws_manager import get_marketdata_hub

load_local_env()
configure_logging()

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

settings = get_settings()

_prefetch_worker = None
_instruments_loader = None
_news_ingestor = None
_pcr_snapshot_service = None
_scanner_alert_scheduler = None
_ai_auto_paper_scheduler = None
_mt5_autonomous_scheduler = None
_adaptive_management_monitor = None
_portfolio_execution_monitor = None
_economic_intelligence_scheduler = None
_prefetch_enabled = (
    os.getenv("BENSIM_PREFETCH_ENABLED")
    or
    os.getenv("OPENTERMINALUI_PREFETCH_ENABLED")
    or os.getenv("OPENSCREENS_PREFETCH_ENABLED")
    or os.getenv("TRADE_SCREENS_PREFETCH_ENABLED")
    or "0"
) == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _prefetch_worker, _instruments_loader, _news_ingestor, _pcr_snapshot_service, _scanner_alert_scheduler, _ai_auto_paper_scheduler, _mt5_autonomous_scheduler, _adaptive_management_monitor, _portfolio_execution_monitor, _economic_intelligence_scheduler
    validate_runtime_secrets()
    init_db()

    from backend.api.deps import get_unified_fetcher
    fetcher = await get_unified_fetcher()

    _prefetch_worker = get_prefetch_worker(fetcher)
    _instruments_loader = get_instruments_loader()
    _news_ingestor = get_news_ingestor()
    _pcr_snapshot_service = get_pcr_snapshot_service()
    _scanner_alert_scheduler = get_scanner_alert_scheduler_service()

    if _prefetch_enabled:
        await _prefetch_worker.start()
    if _instruments_loader:
        await _instruments_loader.start()
    if _news_ingestor:
        await _news_ingestor.start()
    if _pcr_snapshot_service:
        await _pcr_snapshot_service.start()

    hub = get_marketdata_hub()
    await hub.start()

    get_alert_evaluator_service().start(hub)
    get_paper_engine().start(hub)

    if _scanner_alert_scheduler:
        await _scanner_alert_scheduler.start(hub, interval_seconds=900)
    _ai_auto_paper_scheduler = ai_auto_paper_service
    forex_execution_provider = os.getenv("FOREX_EXECUTION_PROVIDER", "").strip().upper()
    ibkr_forex_scheduler_enabled = forex_execution_provider != "MT5"
    if ibkr_forex_scheduler_enabled and _ai_auto_paper_scheduler.config.scheduler_enabled:
        await _ai_auto_paper_scheduler.initialize_broker_readiness(timeout_seconds=45)
        await _ai_auto_paper_scheduler.start_scheduler()
    _mt5_autonomous_scheduler = mt5_autonomous_service
    if _mt5_autonomous_scheduler.config.autonomous_submission_enabled:
        await _mt5_autonomous_scheduler.start()
    _portfolio_execution_monitor = portfolio_manager
    await _portfolio_execution_monitor.start()
    _adaptive_management_monitor = adaptive_management_service
    await _adaptive_management_monitor.start()
    _economic_intelligence_scheduler = economic_intelligence_service
    await _economic_intelligence_scheduler.start()

    yield

    if _economic_intelligence_scheduler:
        await _economic_intelligence_scheduler.stop()
    if _adaptive_management_monitor:
        await _adaptive_management_monitor.stop()
    if _portfolio_execution_monitor:
        await _portfolio_execution_monitor.stop()
    if _mt5_autonomous_scheduler:
        await _mt5_autonomous_scheduler.stop()
    if _ai_auto_paper_scheduler:
        await _ai_auto_paper_scheduler.stop_scheduler()
    if _prefetch_worker:
        await _prefetch_worker.stop()
    if _instruments_loader:
        await _instruments_loader.stop()
    if _news_ingestor:
        await _news_ingestor.stop()
    if _pcr_snapshot_service:
        await _pcr_snapshot_service.stop()
    if _scanner_alert_scheduler:
        await _scanner_alert_scheduler.stop()

    try:
        from backend.brokers import broker_registry

        ibkr = broker_registry.get("ibkr")
        if hasattr(ibkr, "shutdown"):
            await ibkr.shutdown()
    except Exception:
        pass

    await hub.shutdown()
    await shutdown_unified_fetcher()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.middleware("http")(request_context_middleware)


@app.exception_handler(BensimAPIError)
async def bensim_api_error_handler(request: Request, exc: BensimAPIError) -> JSONResponse:
    envelope = APIErrorEnvelope(
        error=APIErrorPayload(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=get_request_id(request),
        )
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))

app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.api.router import api_router

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/livez", tags=["health"])
def livez() -> dict[str, object]:
    return {"status": "ok", "service": "bensim-api"}


async def _readiness_snapshot() -> dict[str, object]:
    from backend.api.deps import get_unified_fetcher
    from backend.shared.cache import cache as cache_instance
    from backend.shared.ws_manager import get_marketdata_hub

    hub = get_marketdata_hub()
    fetcher = await get_unified_fetcher()
    cache_health = await cache_instance.health()
    db_status = "ok"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    dependencies = {
        "database": {"status": db_status, "required": True},
        "cache": cache_health,
        "marketdata_hub": {
            "status": "ok" if hub.is_running else "stopped",
            "required": False,
            "clients": hub.client_count,
            "subscriptions": hub.subscription_count,
        },
        "unified_fetcher": {
            "status": "ok" if fetcher is not None else "error",
            "required": True,
        },
        "services": service_status_registry.snapshot(),
    }
    status = "ok"
    if db_status != "ok" or fetcher is None:
        status = "error"
    elif cache_health.get("l2_redis") == "error" or not hub.is_running:
        status = "degraded"
    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dependencies": dependencies,
    }


@app.get("/readyz", tags=["health"])
async def readyz() -> dict[str, object]:
    return await _readiness_snapshot()


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, object]:
    return await _readiness_snapshot()


@app.get("/metrics-lite", tags=["health"])
def metrics_lite() -> dict[str, object]:
    from backend.shared.ws_manager import get_marketdata_hub
    hub = get_marketdata_hub()
    from backend.bg_services.scanner_alert_scheduler import get_scanner_alert_scheduler_service
    scanner_service = get_scanner_alert_scheduler_service()
    scanner_status = scanner_service.status_snapshot() if scanner_service else {}

    return {
        "ws_clients": hub.client_count,
        "ws_subscriptions": hub.subscription_count,
        "scanner_alert_last_run": scanner_status.get("last_run_at"),
        "scanner_alert_last_status": scanner_status.get("last_status"),
        "scanner_alert_scanned_symbols": scanner_status.get("last_scanned_symbols"),
        "last_kite_stream_status": hub.kite_stream_status(),
    }


_frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
_frontend_app_entry_paths = {
    "login",
    "register",
    "forgot-access",
    "home",
    "stocks",
    "security",
    "commodities",
    "forex",
    "hotlists",
    "dashboard",
    "screener",
    "compare",
    "portfolio",
    "portfolio-lab",
    "mutual-funds",
    "watchlist",
    "news",
    "alerts",
    "paper",
    "risk",
    "correlation",
    "oms",
    "ops",
    "settings",
    "plugins",
    "saved-views",
    "cockpit",
    "model-lab",
    "equity",
    "fno",
    "backtesting",
    "account",
}


@app.get("/{full_path:path}", include_in_schema=False)
def spa_entry(full_path: str) -> FileResponse:
    if full_path.split("/", 1)[0] == "api":
        raise HTTPException(status_code=404, detail="Not found")
    if not _frontend_dist.exists():
        raise HTTPException(status_code=404, detail="Frontend bundle not found")
    requested = _frontend_dist / full_path
    if full_path and requested.exists() and requested.is_file():
        return FileResponse(requested)
    if full_path and requested.exists() and requested.is_dir():
        directory_index = requested / "index.html"
        if directory_index.exists():
            return FileResponse(directory_index)
    if full_path and (Path(full_path).suffix or full_path.startswith("assets/")):
        raise HTTPException(status_code=404, detail="Static asset not found")
    first_segment = full_path.split("/", 1)[0] if full_path else ""
    if first_segment in _frontend_app_entry_paths:
        app_file = _frontend_dist / "app.html"
        if app_file.exists():
            return FileResponse(app_file)
    index_file = _frontend_dist / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="Frontend entrypoint not found")

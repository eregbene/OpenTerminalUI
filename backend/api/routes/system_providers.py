from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from backend.auth.deps import get_current_user
from backend.market_data.registry import get_provider_registry
from backend.market_data.routing import MarketDataRequest, MarketDataRouter
from backend.market_data.capabilities import Capability
from backend.market_data.models import AssetClass
from backend.models.user import User
from backend.shared.db import SessionLocal
from sqlalchemy import text

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


@router.get("/backup-status")
async def backup_status(_: User = Depends(get_current_user)) -> dict[str, object]:
    """Read-only summary of local Postgres backups (scripts/backup_postgres.ps1).

    Added after the 2026-08-07 incident (see docs/CLAUDE_BACKEND_SAFETY.md). Never
    exposes credentials or full filesystem paths beyond the backup basename.
    """
    backup_dir = Path(os.environ.get("BACKUP_DIR_IN_CONTAINER", "/backups"))

    with SessionLocal() as db:
        alembic_revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()

    if not backup_dir.is_dir():
        return {
            "backup_health": "UNKNOWN",
            "reason": "backup directory not visible to the backend container",
            "database_revision": alembic_revision,
        }

    metadata_files = sorted(backup_dir.glob("*.metadata.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not metadata_files:
        return {
            "backup_health": "CRITICAL",
            "reason": "no backups found",
            "database_revision": alembic_revision,
        }

    latest_verified: dict[str, object] | None = None
    for meta_path in metadata_files:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("backup_verified"):
            latest_verified = data
            break

    if latest_verified is None:
        return {
            "backup_health": "CRITICAL",
            "reason": "no verified backups found",
            "database_revision": alembic_revision,
        }

    try:
        backup_time = datetime.fromisoformat(str(latest_verified["timestamp_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        backup_time = None

    age_hours = None
    if backup_time is not None:
        age_hours = round((datetime.now(timezone.utc) - backup_time).total_seconds() / 3600, 1)

    health = "OK"
    if age_hours is not None:
        if age_hours > 72:
            health = "CRITICAL"
        elif age_hours > 24:
            health = "WARN"

    return {
        "last_successful_backup": latest_verified.get("timestamp_utc"),
        "last_verified_backup": latest_verified.get("timestamp_utc"),
        "backup_age_hours": age_hours,
        "database_revision": alembic_revision,
        "git_commit_at_backup": latest_verified.get("git_commit"),
        "backup_path": latest_verified.get("backup_file"),
        "backup_health": health,
    }

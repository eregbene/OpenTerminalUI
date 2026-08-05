from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_db
from backend.api.routes.forex_frameworks import router
from backend.auth.deps import get_current_user
from backend.forex_frameworks.orm import ForexFrameworkSignalORM
from backend.forex_frameworks.registry import registry
from backend.forex_frameworks.service import context_from_snapshot, framework_service
from backend.forex_intelligence.service import ForexIntelligenceService
from backend.shared.db import Base


def _bars(count: int = 96) -> list[dict[str, object]]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    price = 1.08
    rows = []
    for idx in range(count):
        close = price + 0.00012
        rows.append({"timestamp": (start + timedelta(hours=idx)).isoformat(), "open": price, "high": close + 0.0005, "low": price - 0.0005, "close": close, "volume": 1000 + idx, "is_complete": True})
        price = close
    return rows


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[ForexFrameworkSignalORM.__table__])
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_framework_registry_contains_required_ids() -> None:
    ids = {definition.framework_id for definition in registry.definitions()}

    assert {"trend_following", "mean_reversion", "momentum", "breakout", "wyckoff", "ict", "elliott_wave", "gann", "correlation_intermarket"} <= ids
    assert registry.require("elliott_wave").status.value == "RESEARCH"


def test_framework_signals_persist_with_versions() -> None:
    SessionLocal = _session_factory()
    db = SessionLocal()
    snapshot = ForexIntelligenceService().analyze_rows(_bars(), symbol="EURUSD", timeframe="1h").model_dump(mode="json")
    candles = [{"t": int(datetime.fromisoformat(row["timestamp"]).timestamp()), "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]} for row in _bars()]
    ctx = context_from_snapshot(snapshot, candles)
    signals = framework_service.analyze(ctx, ["trend_following", "mean_reversion", "ict"])

    ids = framework_service.persist(db, ctx, signals)
    latest = framework_service.latest(db, symbol="EURUSD", timeframe="1h")

    assert len(ids) == 3
    assert {row["framework_id"] for row in latest} == {"trend_following", "mean_reversion", "ict"}
    assert latest[0]["framework_version"]
    db.close()


def test_framework_registry_api_lists_groups() -> None:
    SessionLocal = _session_factory()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_db] = lambda: SessionLocal()
    client = TestClient(app)

    response = client.get("/api/forex-frameworks")

    assert response.status_code == 200
    groups = {row["group"] for row in response.json()["frameworks"]}
    assert "Smart Money" in groups
    assert "Macro and Intermarket" in groups

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_db
from backend.api.routes.forex_intelligence import router
from backend.auth.deps import get_current_user
from backend.forex_intelligence.orm import ForexFeatureVectorORM
from backend.forex_intelligence.service import ForexIntelligenceService
from backend.shared.db import Base


def _eurusd_bars(count: int = 96) -> list[dict[str, object]]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    price = 1.0800
    for idx in range(count):
        wave = ((idx % 12) - 6) * 0.00012
        drift = idx * 0.000015
        open_ = price
        close = 1.0800 + drift + wave
        high = max(open_, close) + 0.0007 + (0.0002 if idx % 17 == 0 else 0)
        low = min(open_, close) - 0.0006 - (0.0002 if idx % 19 == 0 else 0)
        rows.append(
            {
                "timestamp": (start + timedelta(hours=idx)).isoformat(),
                "open": round(open_, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": 1000 + idx,
                "is_complete": True,
            }
        )
        price = close
    return rows


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[ForexFeatureVectorORM.__table__])
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_forex_intelligence_is_deterministic_and_read_only() -> None:
    service = ForexIntelligenceService()
    first = service.analyze_rows(_eurusd_bars(), symbol="EURUSD", timeframe="1h")
    second = service.analyze_rows(_eurusd_bars(), symbol="EURUSD", timeframe="1h")

    assert first.symbol == "EURUSD"
    assert first.read_only is True
    assert first.snapshot_id == second.snapshot_id
    assert first.current_feature.confluence_score == second.current_feature.confluence_score
    assert first.trade_explanation["risk"]["engine_submits_trades"] is False
    assert first.feature_count == 96


def test_forex_intelligence_supports_major_pairs() -> None:
    service = ForexIntelligenceService()
    snapshot = service.analyze_rows(_eurusd_bars(), symbol="GBPUSD", timeframe="1h")

    assert snapshot.symbol == "GBPUSD"
    assert snapshot.trade_explanation["market_summary"].startswith("GBP/USD")


def test_forex_intelligence_rejects_unsupported_pair() -> None:
    service = ForexIntelligenceService()
    with pytest.raises(ValueError, match="unsupported forex symbol"):
        service.analyze_rows(_eurusd_bars(), symbol="EURJPY", timeframe="1h")


def test_forex_intelligence_feature_store_roundtrip() -> None:
    SessionLocal = _session_factory()
    db = SessionLocal()
    service = ForexIntelligenceService()
    snapshot = service.analyze_rows(_eurusd_bars(), symbol="EURUSD", timeframe="1h", db=db, source_provider="test", source_dataset_id="test:eurusd:1h")
    rows = service.history(db, symbol="EURUSD", timeframe="1h", limit=200)
    latest = service.get_feature_vector(db, snapshot.feature_vector_id or "")

    assert len(rows) == snapshot.feature_count
    assert latest is not None
    assert latest.symbol == "EURUSD"
    assert latest.feature_payload["symbol"] == "EURUSD"
    db.close()


def test_forex_intelligence_feature_rows_do_not_look_ahead() -> None:
    SessionLocal = _session_factory()
    db = SessionLocal()
    service = ForexIntelligenceService()
    snapshot = service.analyze_rows(_eurusd_bars(), symbol="EURUSD", timeframe="1h", db=db, source_provider="test", source_dataset_id="test:lookahead")
    rows = service.history(db, symbol="EURUSD", timeframe="1h", limit=200)
    sweeps = snapshot.market_structure.get("liquidity_sweeps", [])

    for row in rows:
        feature = row.feature_payload
        known_sweeps = [
            sweep for sweep in sweeps
            if sweep.get("confirmation_time") and datetime.fromisoformat(sweep["confirmation_time"].replace("Z", "+00:00")) <= datetime.fromisoformat(feature["timestamp"].replace("Z", "+00:00"))
        ]
        assert feature["liquidity_sweeps"] == len(known_sweeps)
    db.close()


def test_forex_intelligence_api_analyze() -> None:
    SessionLocal = _session_factory()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_db] = lambda: SessionLocal()
    client = TestClient(app)

    response = client.post(
        "/api/forex-intelligence/analyze",
        json={"symbol": "EURUSD", "timeframe": "1h", "bars": _eurusd_bars()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "EURUSD"
    assert payload["read_only"] is True
    assert payload["feature_store_key"]
    assert payload["trade_explanation"]["confidence"] >= 0

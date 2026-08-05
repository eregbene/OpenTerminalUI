from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.market_structure import router
from backend.auth.deps import get_current_user


def _bars() -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    price = 100.0
    for idx in range(30):
        high = price + (idx % 5) + 1
        low = price - (idx % 4) - 1
        close = high - 0.5 if idx % 2 else low + 0.5
        out.append({"timestamp": (start + timedelta(minutes=idx * 15)).isoformat(), "open": price, "high": high, "low": low, "close": close, "volume": 1000 + idx, "is_complete": True})
        price = close
    return out


def test_market_structure_api_requires_valid_bars() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    client = TestClient(app)
    response = client.post("/api/market-structure/analyze", json={"symbol": "TEST", "timeframe": "15m", "profile": "internal", "bars": _bars()})
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "TEST"
    assert payload["configuration_hash"]
    assert "overlays" in payload


def test_configuration_validation_endpoint() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    client = TestClient(app)
    response = client.post("/api/market-structure/configurations/validate", json={"profile": "balanced"})
    assert response.status_code == 200
    assert response.json()["valid"] is True

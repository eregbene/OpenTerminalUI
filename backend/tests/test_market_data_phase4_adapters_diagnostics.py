from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.auth.jwt import create_access_token
from backend.main import app
from backend.market_data.adapters import normalize_legacy_ohlcv, normalize_legacy_quote
from backend.market_data.capabilities import HistoricalCandleRequest, QuoteRequest
from backend.market_data.models import AssetClass, DataStatus


def test_legacy_quote_adapter_marks_fallback_not_realtime() -> None:
    quote = normalize_legacy_quote(
        {"price": 10, "timestamp": "2026-01-01T12:00:00Z", "source": "mock"},
        provider_id="internal-demo",
        request=QuoteRequest(symbol="AAPL", asset_class=AssetClass.EQUITY),
        fallback=True,
    )
    assert quote.price == Decimal("10")
    assert DataStatus.FALLBACK.value in quote.quality.model_dump(mode="json")["status"]


def test_legacy_ohlcv_adapter_preserves_provider_timestamp() -> None:
    rows = normalize_legacy_ohlcv(
        [{"t": 1767270600, "o": 1, "h": 2, "l": 1, "c": 2, "v": 5}],
        provider_id="yahoo",
        request=HistoricalCandleRequest(symbol="AAPL", timeframe="1m", asset_class=AssetClass.EQUITY),
    )
    assert rows[0].open_time == datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    assert rows[0].provider.provider == "yahoo"


def test_provider_diagnostics_do_not_expose_secrets() -> None:
    token = create_access_token("dev-user", "dev@example.com", "admin")
    client = TestClient(app)
    response = client.get("/api/system/providers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in {200, 401}
    if response.status_code == 200:
        body = response.json()
        assert "providers" in body
        assert "api_key" not in str(body).lower()

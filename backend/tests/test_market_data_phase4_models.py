from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.market_data.fixtures import historical_candles, successful_quote
from backend.market_data.models import DataOrigin, DataQualityMetadata, DataStatus, OHLCVBar, ProviderMetadata


def test_quote_serializes_decimal_and_provenance() -> None:
    quote = successful_quote("MSFT")
    payload = quote.model_dump(mode="json")
    assert payload["symbol"] == "MSFT"
    assert payload["price"] == "123.45"
    assert payload["provider"]["provider"] == "fixture"
    assert "realtime" in payload["quality"]["status"]


def test_ohlcv_requires_timezone_aware_timestamps() -> None:
    provider = ProviderMetadata(provider="fixture", requested_data_type="ohlcv", returned_data_type="ohlcv")
    quality = DataQualityMetadata(status=[DataStatus.HISTORICAL], origin=DataOrigin.PROVIDER)
    with pytest.raises(ValueError):
        OHLCVBar(
            symbol="AAPL",
            timeframe="1m",
            open_time=datetime(2026, 1, 1, 9, 30),
            close_time=datetime(2026, 1, 1, 9, 31, tzinfo=timezone.utc),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            provider=provider,
            quality=quality,
        )


def test_historical_fixture_has_complete_utc_bars() -> None:
    bars = historical_candles()
    assert len(bars) == 5
    assert bars[0].open_time.tzinfo is not None
    assert bars[0].close_time - bars[0].open_time == timedelta(minutes=1)

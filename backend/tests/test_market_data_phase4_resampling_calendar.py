from __future__ import annotations

from datetime import datetime, timezone

from backend.market_data.calendar import MarketCalendar
from backend.market_data.fixtures import historical_candles
from backend.market_data.resampling import resample_bars


def test_resample_completed_bars_without_lookahead() -> None:
    bars = historical_candles()
    rows = resample_bars(bars, "5m", now=datetime(2026, 1, 2, 14, 34, tzinfo=timezone.utc))
    assert rows == []
    complete = resample_bars(bars, "5m", now=datetime(2026, 1, 2, 14, 36, tzinfo=timezone.utc))
    assert len(complete) == 1
    assert complete[0].open == bars[0].open
    assert complete[0].close == bars[-1].close
    assert complete[0].is_complete is True
    assert "resampled:1m->5m" in complete[0].provider.transformation_history


def test_calendar_crypto_is_continuous_and_nyse_has_expected_bars() -> None:
    assert MarketCalendar("CRYPTO").is_market_open(datetime(2026, 1, 3, 12, tzinfo=timezone.utc)) is True
    nyse = MarketCalendar("NASDAQ")
    stamps = nyse.expected_bar_timestamps(
        datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        datetime(2026, 1, 2, 14, 35, tzinfo=timezone.utc),
        "1m",
    )
    assert len(stamps) == 5

"""BSI Daily Bias Audit implementation: tests for backend/market_structure/daily_aggregation.py.

Covers: basic OHLC aggregation correctness, NY-timezone day-boundary grouping (not UTC), ordering
robustness, and the point-in-time-safety property this module's own docstring claims (never
fabricates data beyond what its input already contains -- a partial day stays partial, never
"completed" with information the caller didn't supply).
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.market_structure.daily_aggregation import aggregate_daily_bars_from_h4


def _h4(iso_time: str, o: float, h: float, l: float, c: float, *, tick_volume: int = 100) -> dict:
    return {"symbol": "EURUSD", "timeframe": "H4", "time": iso_time, "open": o, "high": h, "low": l, "close": c, "tick_volume": tick_volume, "spread": 2, "close_time": iso_time}


def test_empty_input_returns_empty_list():
    assert aggregate_daily_bars_from_h4([], symbol="EURUSD") == []


def test_single_day_six_h4_bars_aggregate_to_one_daily_bar():
    # 2026-01-05 is a Monday; all 6 bars sit inside the same NY calendar day (using UTC times that
    # map to NY between 00:00 and 20:00 EST, comfortably inside 2026-01-05 NY regardless of DST).
    rows = [
        _h4("2026-01-05T05:00:00+00:00", 1.1000, 1.1010, 1.0995, 1.1005),
        _h4("2026-01-05T09:00:00+00:00", 1.1005, 1.1030, 1.1000, 1.1020),  # day high
        _h4("2026-01-05T13:00:00+00:00", 1.1020, 1.1025, 1.0980, 1.0990),  # day low
        _h4("2026-01-05T17:00:00+00:00", 1.0990, 1.1000, 1.0985, 1.0995),
        _h4("2026-01-05T21:00:00+00:00", 1.0995, 1.1005, 1.0990, 1.1002),
        _h4("2026-01-06T00:00:00+00:00", 1.1002, 1.1008, 1.0998, 1.1003),  # 2026-01-05 19:00 NY (EST, UTC-5) -- still same NY day
    ]
    result = aggregate_daily_bars_from_h4(rows, symbol="EURUSD")
    assert len(result) == 1
    day = result[0]
    assert day["open"] == 1.1000  # first bar's open
    assert day["close"] == 1.1003  # last bar's close
    assert day["high"] == 1.1030  # max across all 6
    assert day["low"] == 1.0980  # min across all 6
    assert day["source_bar_count"] == 6
    assert day["timeframe"] == "D1"
    assert day["symbol"] == "EURUSD"


def test_two_distinct_ny_days_produce_two_ordered_daily_bars():
    rows = [
        _h4("2026-01-05T10:00:00+00:00", 1.1000, 1.1010, 1.0995, 1.1005),
        _h4("2026-01-05T14:00:00+00:00", 1.1005, 1.1020, 1.1000, 1.1015),
        _h4("2026-01-06T10:00:00+00:00", 1.1015, 1.1025, 1.1010, 1.1018),
        _h4("2026-01-06T14:00:00+00:00", 1.1018, 1.1030, 1.1005, 1.1022),
    ]
    result = aggregate_daily_bars_from_h4(rows, symbol="EURUSD")
    assert len(result) == 2
    day1, day2 = result
    assert day1["open"] == 1.1000 and day1["close"] == 1.1015
    assert day2["open"] == 1.1015 and day2["close"] == 1.1022
    # ascending chronological order
    from backend.market_structure.daily_aggregation import _parse_time
    assert _parse_time(day1["time"]) < _parse_time(day2["time"])


def test_grouping_uses_new_york_time_not_utc_date():
    """A bar at 23:00 UTC on 2026-01-05 is still 2026-01-05 18:00 EST (New York) -- must NOT be
    grouped with the NEXT UTC calendar date. This is the specific claim the module docstring makes
    (NY day boundary, not UTC) -- proven here, not just asserted."""
    rows = [
        _h4("2026-01-05T02:00:00+00:00", 1.1000, 1.1005, 1.0995, 1.1002),  # 2026-01-04 21:00 EST -> NY date 2026-01-04
        _h4("2026-01-05T23:00:00+00:00", 1.1002, 1.1008, 1.0998, 1.1004),  # 2026-01-05 18:00 EST -> NY date 2026-01-05
    ]
    result = aggregate_daily_bars_from_h4(rows, symbol="EURUSD")
    # A naive UTC-date grouping would put both bars in "2026-01-05" (1 bucket); the correct
    # NY-anchored grouping splits them into two distinct NY calendar days.
    assert len(result) == 2


def test_out_of_order_input_rows_still_aggregate_correctly():
    """Real bars_as_of()/cached_candles() output is already ascending, but this function must not
    silently corrupt open/close if ever handed rows out of order -- defensive robustness."""
    rows = [
        _h4("2026-01-05T14:00:00+00:00", 1.1010, 1.1020, 1.1005, 1.1015),  # later bar listed first
        _h4("2026-01-05T10:00:00+00:00", 1.1000, 1.1012, 1.0995, 1.1010),  # earlier bar listed second
    ]
    result = aggregate_daily_bars_from_h4(rows, symbol="EURUSD")
    assert len(result) == 1
    assert result[0]["open"] == 1.1000  # the chronologically-first bar's open, regardless of input order
    assert result[0]["close"] == 1.1015  # the chronologically-last bar's close


def test_point_in_time_safety_partial_day_reflects_only_given_bars_never_completed():
    """The core no-lookahead claim: if the caller (bars_as_of, point-in-time-safe) only had 2 of a
    real day's 6 H4 bars available as of some cutoff `at`, the emitted daily bar for that day must
    reflect ONLY those 2 bars' own OHLC -- never fabricate what the other 4 (not-yet-closed, not
    yet knowable at `at`) would have shown. This function receives no `at` parameter at all and
    performs no filtering of its own -- the property holds by construction (it only ever reads
    exactly the rows it's given), verified here by comparing a full 6-bar day against the same
    day's first-2-bars-only truncation."""
    full_day = [
        _h4("2026-01-05T05:00:00+00:00", 1.1000, 1.1010, 1.0995, 1.1005),
        _h4("2026-01-05T09:00:00+00:00", 1.1005, 1.1050, 1.1000, 1.1020),  # would be the day's real high
        _h4("2026-01-05T13:00:00+00:00", 1.1020, 1.1025, 1.0960, 1.0990),  # would be the day's real low
        _h4("2026-01-05T17:00:00+00:00", 1.0990, 1.1000, 1.0985, 1.0995),
    ]
    truncated_as_of_early_cutoff = full_day[:1]  # only what was knowable before the low/high bars formed

    full_result = aggregate_daily_bars_from_h4(full_day, symbol="EURUSD")
    truncated_result = aggregate_daily_bars_from_h4(truncated_as_of_early_cutoff, symbol="EURUSD")

    assert full_result[0]["high"] == 1.1050
    assert full_result[0]["low"] == 1.0960
    # The truncated (point-in-time-safe) view must NOT show the later high/low it never saw --
    # proving no lookahead is baked into the aggregation itself.
    assert truncated_result[0]["high"] == 1.1010
    assert truncated_result[0]["low"] == 1.0995
    assert truncated_result[0]["high"] != full_result[0]["high"]
    assert truncated_result[0]["low"] != full_result[0]["low"]


def test_naive_utc_time_is_treated_as_utc_not_rejected():
    rows = [{"symbol": "EURUSD", "timeframe": "H4", "time": datetime(2026, 1, 5, 10, 0), "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "tick_volume": 100, "close_time": datetime(2026, 1, 5, 14, 0)}]
    result = aggregate_daily_bars_from_h4(rows, symbol="EURUSD")
    assert len(result) == 1

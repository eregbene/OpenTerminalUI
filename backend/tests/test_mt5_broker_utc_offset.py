"""2026-08-19: root-cause fix for the ~3h skew found while investigating why realized_pnl was
permanently NULL on closed trades (see test_mt5_outcome_resolution_retry.py). MT5's raw "time"
fields (candles/positions/history deals&orders/quotes) are the broker SERVER's wall clock, not
UTC -- confirmed in production: 529 of 534 tracked positions showed closed_detected_at earlier
than opened_at, a chronological impossibility, because opened_at was parsed from MT5's raw
epoch AS IF it were UTC while closed_detected_at came from Python's real utcnow().

These tests cover MT5Client.broker_utc_offset() (dynamic detection, rounding, caching, fail-
open behavior) and confirm each of the five raw-timestamp parsers (candles/positions/history/
orders/quotes) actually applies the offset it's given, using the same fake_adapter() fixture
already used throughout the MT5 test suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.brokers.mt5.candles import candle_from_raw
from backend.brokers.mt5.history import history_item_from_raw
from backend.brokers.mt5.orders import order_from_raw
from backend.brokers.mt5.positions import position_from_raw
from backend.brokers.mt5.quotes import quote_from_tick
from backend.tests.test_mt5_adapter import FakeMT5, FakeRow, fake_adapter


class _CountingTickMT5(FakeMT5):
    def __init__(self, *, tick_time: int, raise_on_tick: bool = False):
        super().__init__()
        self._tick_time = tick_time
        self._raise_on_tick = raise_on_tick
        self.tick_calls = 0

    def symbol_info_tick(self, symbol):
        self.tick_calls += 1
        if self._raise_on_tick:
            raise RuntimeError("simulated broker outage")
        return FakeRow(time=self._tick_time, bid=1.1, ask=1.1002, last=1.1001)


def _adapter_with_tick_time(tick_time: int) -> tuple:
    adapter = fake_adapter()
    fake_mt5 = _CountingTickMT5(tick_time=tick_time)
    adapter.client._mt5 = fake_mt5
    return adapter, fake_mt5


def test_offset_detected_and_rounded_to_nearest_15_minutes():
    now = datetime.now(timezone.utc)
    # 3h07m ahead -- should round DOWN to the nearest 15-minute mark (3h00m), absorbing jitter.
    tick_time = int((now + timedelta(hours=3, minutes=7)).timestamp())
    adapter, _fake = _adapter_with_tick_time(tick_time)

    offset = adapter.client.broker_utc_offset()

    assert offset == timedelta(hours=3)


def test_offset_rounds_up_when_past_the_midpoint():
    now = datetime.now(timezone.utc)
    tick_time = int((now + timedelta(hours=3, minutes=8)).timestamp())
    adapter, _fake = _adapter_with_tick_time(tick_time)

    offset = adapter.client.broker_utc_offset()

    assert offset == timedelta(hours=3, minutes=15)


def test_cache_avoids_repeated_broker_calls():
    now = datetime.now(timezone.utc)
    tick_time = int((now + timedelta(hours=3)).timestamp())
    adapter, fake_mt5 = _adapter_with_tick_time(tick_time)

    first = adapter.client.broker_utc_offset()
    second = adapter.client.broker_utc_offset()

    assert first == second == timedelta(hours=3)
    assert fake_mt5.tick_calls == 1


def test_cache_expires_and_recomputes(monkeypatch):
    monkeypatch.setenv("MT5_BROKER_UTC_OFFSET_CACHE_SECONDS", "1")
    now = datetime.now(timezone.utc)
    tick_time = int((now + timedelta(hours=3)).timestamp())
    adapter, fake_mt5 = _adapter_with_tick_time(tick_time)

    adapter.client.broker_utc_offset()
    assert fake_mt5.tick_calls == 1
    adapter.client._broker_utc_offset_computed_at = now - timedelta(seconds=5)

    adapter.client.broker_utc_offset()

    assert fake_mt5.tick_calls == 2


def test_fails_open_to_zero_on_first_ever_failure():
    adapter, _fake = _adapter_with_tick_time(0)
    adapter.client._mt5 = _CountingTickMT5(tick_time=0, raise_on_tick=True)

    offset = adapter.client.broker_utc_offset()

    assert offset == timedelta(0)


def test_fails_open_to_previous_value_on_later_failure(monkeypatch):
    monkeypatch.setenv("MT5_BROKER_UTC_OFFSET_CACHE_SECONDS", "1")
    now = datetime.now(timezone.utc)
    tick_time = int((now + timedelta(hours=3)).timestamp())
    adapter, _fake = _adapter_with_tick_time(tick_time)
    good = adapter.client.broker_utc_offset()
    assert good == timedelta(hours=3)

    adapter.client._mt5 = _CountingTickMT5(tick_time=0, raise_on_tick=True)
    adapter.client._broker_utc_offset_computed_at = now - timedelta(seconds=5)

    offset = adapter.client.broker_utc_offset()

    assert offset == timedelta(hours=3)  # kept the last known-good value, didn't reset to zero


# --- each raw-timestamp parser actually applies the offset it's given ---

def test_candle_from_raw_applies_offset():
    raw_epoch = 1_800_000_000
    unadjusted = candle_from_raw("EURUSD", "M5", {"time": raw_epoch, "open": 1, "high": 1, "low": 1, "close": 1}, complete_override=True)
    adjusted = candle_from_raw("EURUSD", "M5", {"time": raw_epoch, "open": 1, "high": 1, "low": 1, "close": 1}, complete_override=True, broker_utc_offset=timedelta(hours=3))

    assert unadjusted.time - adjusted.time == timedelta(hours=3)


def test_position_from_raw_applies_offset():
    raw_epoch = 1_800_000_000
    row = FakeRow(ticket=1, symbol="EURUSD", type=0, volume=1.0, price_open=1.09, price_current=1.1, profit=10.0, time=raw_epoch)
    unadjusted = position_from_raw(row)
    adjusted = position_from_raw(row, broker_utc_offset=timedelta(hours=3))

    assert unadjusted.time - adjusted.time == timedelta(hours=3)


def test_history_item_from_raw_applies_offset():
    raw_epoch = 1_800_000_000
    row = FakeRow(ticket=3, order=2, symbol="EURUSD", type=0, volume=1.0, price=1.1, profit=5.0, commission=-0.5, time=raw_epoch)
    unadjusted = history_item_from_raw(row)
    adjusted = history_item_from_raw(row, broker_utc_offset=timedelta(hours=3))

    assert unadjusted.time - adjusted.time == timedelta(hours=3)


def test_order_from_raw_applies_offset():
    raw_epoch = 1_800_000_000
    row = FakeRow(ticket=2, symbol="EURUSD", type=2, volume_current=1.0, price_open=1.08, sl=1.07, tp=1.11, time_setup=raw_epoch)
    unadjusted = order_from_raw(row)
    adjusted = order_from_raw(row, broker_utc_offset=timedelta(hours=3))

    assert unadjusted.time_setup - adjusted.time_setup == timedelta(hours=3)


def test_quote_from_tick_applies_offset():
    raw_epoch = 1_800_000_000
    tick = FakeRow(time=raw_epoch, bid=1.1, ask=1.1002, last=1.1001)
    unadjusted = quote_from_tick("EURUSD", tick)
    adjusted = quote_from_tick("EURUSD", tick, broker_utc_offset=timedelta(hours=3))

    assert unadjusted.time - adjusted.time == timedelta(hours=3)


def test_omitting_offset_defaults_to_zero_unadjusted():
    raw_epoch = 1_800_000_000
    row = FakeRow(ticket=1, symbol="EURUSD", type=0, volume=1.0, price_open=1.09, price_current=1.1, profit=10.0, time=raw_epoch)
    result = position_from_raw(row)

    assert result.time == datetime.fromtimestamp(raw_epoch, tz=timezone.utc)

"""Regression tests for ForexSBHistoricalProvider (ForexSB integration directive): binary
record decoding (24-byte and 28-byte little-endian formats), Part 3's per-record validation
(OHLC consistency, positive price, implausible date), corrupt-record-size handling, and the
M30->H1/H4/D1 deterministic resample (Part 5) -- including its explicit no-fabrication-across-
gaps guarantee."""
from __future__ import annotations

import struct
from datetime import datetime, timezone

from backend.historical_intelligence.providers.forexsb_provider import (
    _derive_higher_timeframe, _parse_records,
)
from backend.historical_intelligence.providers.base import HistoricalBar
from backend.historical_intelligence.providers.forexsb_provider import ForexSBHistoricalProvider

_EPOCH_SECONDS = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())


def _pack_record(*, minutes_since_epoch: int, open_i: int, high_i: int, low_i: int, close_i: int, volume: int = 100, spread: int = 5, with_spread: bool = True) -> bytes:
    if with_spread:
        return struct.pack("<iiiiiii", minutes_since_epoch, open_i, high_i, low_i, close_i, volume, spread)
    # Legacy 24-byte record: 6 packed int32 fields, no spread (24 / 4 = 6 exactly).
    return struct.pack("<iiiiii", minutes_since_epoch, open_i, high_i, low_i, close_i, volume)


def _minutes_for(dt: datetime) -> int:
    return int((dt.timestamp() - _EPOCH_SECONDS) // 60)


def test_parse_records_decodes_28_byte_records_with_correct_prices():
    t = datetime(2020, 6, 15, 10, 0, tzinfo=timezone.utc)
    raw = _pack_record(minutes_since_epoch=_minutes_for(t), open_i=110000, high_i=110050, low_i=109950, close_i=110020, volume=500, spread=8)
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert len(bars) == 1
    b = bars[0]
    assert b.time == t
    assert abs(b.open - 1.10000) < 1e-9
    assert abs(b.high - 1.10050) < 1e-9
    assert abs(b.low - 1.09950) < 1e-9
    assert abs(b.close - 1.10020) < 1e-9
    assert b.tick_volume == 500
    assert b.spread == 8


def test_parse_records_decodes_24_byte_legacy_records():
    t = datetime(2011, 3, 1, 0, 0, tzinfo=timezone.utc)
    raw = _pack_record(minutes_since_epoch=_minutes_for(t), open_i=1500, high_i=1510, low_i=1490, close_i=1505, with_spread=False)
    bars = _parse_records(raw, price_scale=1000.0, volume_scale=1, broker_symbol="USDJPY", period_label="M30")
    assert len(bars) == 1
    assert bars[0].spread == 0
    assert abs(bars[0].close - 1.505) < 1e-9


def test_parse_records_rejects_ohlc_inconsistent_record():
    t = datetime(2020, 1, 1, tzinfo=timezone.utc)
    # low (109000) is ABOVE close (109500 vs high 109000) -- inconsistent: high < close
    raw = _pack_record(minutes_since_epoch=_minutes_for(t), open_i=109200, high_i=109000, low_i=108900, close_i=109500)
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert bars == []


def test_parse_records_rejects_non_positive_price():
    t = datetime(2020, 1, 1, tzinfo=timezone.utc)
    raw = _pack_record(minutes_since_epoch=_minutes_for(t), open_i=0, high_i=100, low_i=0, close_i=50)
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert bars == []


def test_parse_records_rejects_implausible_pre_1990_date():
    # A corrupt/garbage time field decoding to a date before any real market data.
    raw = _pack_record(minutes_since_epoch=-100_000_000, open_i=110000, high_i=110050, low_i=109950, close_i=110020)
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert bars == []


def test_parse_records_corrupt_size_returns_empty_not_partial_garbage():
    raw = b"\x00" * 25  # divides evenly by neither 24 nor 28
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert bars == []


def test_parse_records_sorts_and_dedupes_by_timestamp():
    t1 = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2020, 1, 1, 0, 30, tzinfo=timezone.utc)
    raw = (
        _pack_record(minutes_since_epoch=_minutes_for(t2), open_i=110000, high_i=110050, low_i=109950, close_i=110020)
        + _pack_record(minutes_since_epoch=_minutes_for(t1), open_i=109000, high_i=109050, low_i=108950, close_i=109020)
    )
    bars = _parse_records(raw, price_scale=100000.0, volume_scale=1, broker_symbol="EURUSD", period_label="M30")
    assert [b.time for b in bars] == [t1, t2]


def _m30_bar(dt: datetime, close: float, *, high: float | None = None, low: float | None = None, volume: int = 10, spread: int = 3) -> HistoricalBar:
    return HistoricalBar(time=dt, open=close, high=high if high is not None else close + 0.001, low=low if low is not None else close - 0.001, close=close, tick_volume=volume, spread=spread)


def test_derive_higher_timeframe_h1_aggregates_two_m30_bars_correctly():
    bars = [
        _m30_bar(datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc), close=1.1000, high=1.1010, low=1.0990, volume=10, spread=2),
        _m30_bar(datetime(2020, 1, 1, 10, 30, tzinfo=timezone.utc), close=1.1050, high=1.1080, low=1.1000, volume=15, spread=5),
    ]
    h1 = _derive_higher_timeframe(bars, 60)
    assert len(h1) == 1
    bar = h1[0]
    assert bar.time == datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert bar.open == 1.1000  # first bar's open
    assert bar.high == 1.1080  # max high
    assert bar.low == 1.0990   # min low
    assert bar.close == 1.1050  # last bar's close
    assert bar.tick_volume == 25  # sum
    assert bar.spread == 5  # max, conservative


def test_derive_higher_timeframe_never_fabricates_bucket_across_a_gap():
    """A weekend/holiday gap in the M30 series must produce NO derived candle for the empty
    period -- never a synthetic flat bar (Part 5's explicit no-lookahead/no-fabrication rule)."""
    bars = [
        _m30_bar(datetime(2020, 1, 3, 20, 0, tzinfo=timezone.utc), close=1.1000),  # Friday
        # weekend gap: no bars for all of Saturday/most of Sunday
        _m30_bar(datetime(2020, 1, 5, 22, 0, tzinfo=timezone.utc), close=1.1050),  # Sunday evening
    ]
    h4 = _derive_higher_timeframe(bars, 240)
    # Exactly 2 derived candles (one per real bar's bucket) -- nothing fabricated in between.
    assert len(h4) == 2
    assert h4[0].time < h4[1].time
    gap = h4[1].time - h4[0].time
    assert gap.total_seconds() > 240 * 60  # the gap is wider than one H4 period, proving no filler bars were inserted


def test_derive_higher_timeframe_empty_input_returns_empty():
    assert _derive_higher_timeframe([], 60) == []


def test_provider_is_never_a_proxy():
    provider = ForexSBHistoricalProvider()
    for symbol in ("EURUSD", "XAUUSD", "GBPJPY"):
        assert provider.is_proxy_for(symbol) is False


def test_provider_unsupported_timeframe_returns_empty_not_raises():
    import asyncio

    provider = ForexSBHistoricalProvider()

    async def fake_metadata():
        return {"EURUSD": {"priceScale": 100000, "volumeScale": 1, "digits": 5}}

    provider._get_metadata = fake_metadata  # type: ignore[method-assign]
    result = asyncio.run(provider.fetch_bars(
        canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M2",
        start=datetime(2020, 1, 1, tzinfo=timezone.utc), end=datetime(2020, 1, 2, tzinfo=timezone.utc),
    ))
    assert result == []


def test_provider_unknown_symbol_returns_empty_not_raises():
    import asyncio

    provider = ForexSBHistoricalProvider()

    async def fake_metadata():
        return {}

    provider._get_metadata = fake_metadata  # type: ignore[method-assign]
    result = asyncio.run(provider.fetch_bars(
        canonical_symbol="ZZZINVALID", broker_symbol="ZZZINVALID", timeframe="M30",
        start=datetime(2020, 1, 1, tzinfo=timezone.utc), end=datetime(2020, 1, 2, tzinfo=timezone.utc),
    ))
    assert result == []

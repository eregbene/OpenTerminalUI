"""Tests for backend/market_structure/volume_delta.py -- independently-implemented cumulative
volume delta + price/CVD divergence (public TA concept, see docs/
EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md's LuxAlgo section). Pure-function tests only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.volume_delta import bar_delta, cumulative_delta, divergence_states

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, *, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> StructureBar:
    return StructureBar(
        index=i, symbol="TEST", timeframe="M15",
        open_time=_T0 + timedelta(minutes=15 * i), close_time=_T0 + timedelta(minutes=15 * (i + 1)),
        open=Decimal(str(open_)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


def test_bar_delta_sign_matches_bar_direction():
    bars = [
        _bar(0, open_=100, high=101, low=99, close=100.5, volume=50),   # bullish
        _bar(1, open_=100.5, high=101, low=99.5, close=100.0, volume=30),  # bearish
        _bar(2, open_=100, high=100, low=100, close=100, volume=20),   # doji
    ]
    deltas = bar_delta(bars)
    assert deltas[0] == 50.0
    assert deltas[1] == -30.0
    assert deltas[2] == 0.0


def test_cumulative_delta_is_running_sum():
    bars = [
        _bar(0, open_=100, high=101, low=99, close=100.5, volume=50),
        _bar(1, open_=100.5, high=101, low=99.5, close=100.0, volume=30),
        _bar(2, open_=100, high=101, low=99, close=100.8, volume=10),
    ]
    cvd = cumulative_delta(bars)
    assert cvd == [50.0, 20.0, 30.0]


def test_divergence_warmup_is_none():
    bars = [_bar(i, open_=100, high=101, low=99, close=100.5) for i in range(10)]
    cvd = cumulative_delta(bars)
    states = divergence_states(bars, cvd, lookback=20)
    assert all(s is None for s in states)


def test_bearish_divergence_detected_price_higher_high_cvd_lower_high():
    # Build a window where the LAST bar makes the highest price high in the window, but its
    # own volume is small/bearish-biased so CVD's peak occurred earlier in the window.
    bars = []
    for i in range(19):
        bars.append(_bar(i, open_=100, high=100.5, low=99.5, close=100.4, volume=200))  # strong bullish volume, modest highs
    # earlier bar with a genuine CVD peak
    bars[10] = _bar(10, open_=100, high=100.6, low=99.5, close=100.5, volume=500)
    # final bar: new price HIGH, but bearish-bodied (adds negative volume) -> CVD does not confirm
    bars.append(_bar(19, open_=100.4, high=105.0, low=100.0, close=100.1, volume=50))
    cvd = cumulative_delta(bars)
    states = divergence_states(bars, cvd, lookback=20)
    assert states[19] is not None
    assert states[19].bearish is True
    assert states[19].bullish is False


def test_no_divergence_when_price_and_cvd_agree():
    bars = [_bar(i, open_=100 + i * 0.1, high=100.1 + i * 0.1, low=99.9 + i * 0.1, close=100.15 + i * 0.1, volume=100) for i in range(25)]
    cvd = cumulative_delta(bars)
    states = divergence_states(bars, cvd, lookback=20)
    assert states[24] is not None
    assert states[24].bullish is False
    assert states[24].bearish is False

"""Tests for backend/market_structure/oscillators.py -- the independently-implemented RSI/
WaveTrend/CCI/ADX feature set (jdehorty Lorentzian Classification's public methodology, see
docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md). Pure-function tests only; not wired into any
strategy or live decision yet."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.oscillators import (
    adx, cci, lorentzian_feature_series, rolling_minmax_normalize, rsi, wave_trend,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, *, open_: float, high: float, low: float, close: float) -> StructureBar:
    return StructureBar(
        index=i, symbol="TEST", timeframe="M15",
        open_time=_T0 + timedelta(minutes=15 * i), close_time=_T0 + timedelta(minutes=15 * (i + 1)),
        open=Decimal(str(round(open_, 5))), high=Decimal(str(round(high, 5))),
        low=Decimal(str(round(low, 5))), close=Decimal(str(round(close, 5))), volume=Decimal("100"),
    )


def _trending_bars(n: int = 100, seed: int = 3, drift: float = 0.15) -> list[StructureBar]:
    rnd = random.Random(seed)
    bars = []
    price = 100.0
    for i in range(n):
        price += drift + rnd.uniform(-0.05, 0.05)
        o = price - rnd.uniform(0.02, 0.05)
        bars.append(_bar(i, open_=o, high=max(o, price) + 0.05, low=min(o, price) - 0.05, close=price))
    return bars


def _choppy_bars(n: int = 100, seed: int = 5) -> list[StructureBar]:
    rnd = random.Random(seed)
    bars = []
    for i in range(n):
        c = 100.0 + rnd.uniform(-0.3, 0.3)
        o = 100.0 + rnd.uniform(-0.3, 0.3)
        bars.append(_bar(i, open_=o, high=max(o, c) + 0.1, low=min(o, c) - 0.1, close=c))
    return bars


def test_rsi_warmup_then_bounded_0_100():
    bars = _trending_bars()
    values = rsi(bars, period=14)
    assert all(v is None for v in values[:14])
    assert all(v is not None for v in values[14:])
    assert all(0.0 <= v <= 100.0 for v in values[14:])


def test_rsi_strongly_uptrending_market_is_high():
    bars = _trending_bars(drift=0.5)
    values = rsi(bars, period=14)
    tail = [v for v in values[-20:] if v is not None]
    assert all(v > 60.0 for v in tail), tail


def test_rsi_strongly_downtrending_market_is_low():
    bars = _trending_bars(drift=-0.5)
    values = rsi(bars, period=14)
    tail = [v for v in values[-20:] if v is not None]
    assert all(v < 40.0 for v in tail), tail


def test_cci_warmup_then_populated():
    bars = _choppy_bars()
    values = cci(bars, period=20)
    assert all(v is None for v in values[:19])
    assert all(v is not None for v in values[19:])


def test_wave_trend_warmup_then_populated():
    bars = _choppy_bars()
    values = wave_trend(bars)
    assert values[0] is None
    assert any(v is not None for v in values[-20:])


def test_adx_warmup_then_bounded_0_100():
    bars = _trending_bars()
    values = adx(bars, period=14)
    populated = [v for v in values if v is not None]
    assert populated
    assert all(0.0 <= v <= 100.0 for v in populated)


def test_adx_trending_market_exceeds_choppy_market():
    trending = adx(_trending_bars(n=120, drift=0.6), period=14)
    choppy = adx(_choppy_bars(n=120), period=14)
    trending_tail = [v for v in trending[-20:] if v is not None]
    choppy_tail = [v for v in choppy[-20:] if v is not None]
    assert trending_tail and choppy_tail
    assert (sum(trending_tail) / len(trending_tail)) > (sum(choppy_tail) / len(choppy_tail))


def test_rolling_minmax_normalize_is_bounded_0_1_and_causal():
    values: list[float | None] = [None, None] + [float(i) for i in range(50)]
    out = rolling_minmax_normalize(values, window=10)
    assert out[0] is None and out[1] is None
    assert all(0.0 <= v <= 1.0 for v in out[2:])
    # Strictly increasing input -> the most recent point in any full window is always the max -> 1.0
    assert out[-1] == 1.0


def test_rolling_minmax_normalize_flat_series_is_midpoint_not_a_crash():
    values = [5.0] * 30
    out = rolling_minmax_normalize(values, window=10)
    assert all(v == 0.5 for v in out)


def test_lorentzian_feature_series_length_and_shape():
    bars = _trending_bars(n=150)
    features = lorentzian_feature_series(bars, normalize_window=50)
    assert len(features) == len(bars)
    populated = [f for f in features if f is not None]
    assert populated
    for f in populated:
        assert len(f) == 4
        assert all(0.0 <= x <= 1.0 for x in f)


def test_lorentzian_feature_series_none_during_shared_warmup():
    bars = _trending_bars(n=10)  # far short of every feature's own warmup
    features = lorentzian_feature_series(bars)
    assert all(f is None for f in features)

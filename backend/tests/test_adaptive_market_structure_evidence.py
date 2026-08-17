"""Tests for adaptive_management/service.py::_market_structure_evidence -- the 2026-08-17
observability-only wiring of squeeze/EQH-EQL context into the Adaptive Trade Manager's own
per-cycle position evaluation. Pure-function tests only; this data is attached to `context` for
audit/future-validation visibility and is NEVER read by any trailing-stop/exit/risk decision."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from backend.adaptive_management.service import _MARKET_STRUCTURE_EVIDENCE_MIN_BARS, _market_structure_evidence

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, *, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "time": (_T0 + timedelta(minutes=5 * i)).isoformat(),
        "open": open_, "high": high, "low": low, "close": close, "volume": 100,
    }


def _consolidation_then_breakout_candles(seed: int = 11, consolidation_n: int = 60, breakout_n: int = 30) -> list[dict]:
    rnd = random.Random(seed)
    candles = []
    price = 100.0
    for i in range(consolidation_n):
        c = 100.0 + rnd.uniform(-0.02, 0.02)
        o = 100.0 + rnd.uniform(-0.02, 0.02)
        candles.append(_candle(i, open_=o, high=max(o, c) + 0.08, low=min(o, c) - 0.08, close=c))
    for i in range(consolidation_n, consolidation_n + breakout_n):
        price += rnd.uniform(0.25, 0.45)
        o = price - rnd.uniform(0.1, 0.2)
        candles.append(_candle(i, open_=o, high=max(o, price) + 0.15, low=min(o, price) - 0.05, close=price))
    return candles


def test_returns_none_below_minimum_bar_count():
    candles = _consolidation_then_breakout_candles(consolidation_n=10, breakout_n=5)
    assert len(candles) < _MARKET_STRUCTURE_EVIDENCE_MIN_BARS
    assert _market_structure_evidence(candles, symbol="EURUSD", timeframe="M5") is None


def test_returns_shape_with_squeeze_and_eqh_eql_fields():
    candles = _consolidation_then_breakout_candles()
    result = _market_structure_evidence(candles, symbol="EURUSD", timeframe="M5")
    assert result is not None
    assert result["timeframe"] == "M5"
    assert set(result.keys()) == {"timeframe", "squeeze_state", "squeeze_momentum_value", "eqh_active", "eql_active"}
    assert result["squeeze_state"] in ("ON", "RELEASING", "OFF", None)
    assert isinstance(result["eqh_active"], bool)
    assert isinstance(result["eql_active"], bool)


def test_malformed_candles_fail_open_never_raises():
    malformed = [{"time": "not-a-timestamp"} for _ in range(_MARKET_STRUCTURE_EVIDENCE_MIN_BARS + 5)]
    result = _market_structure_evidence(malformed, symbol="EURUSD", timeframe="M5")
    assert result is not None
    assert result.get("unavailable") is True


def test_never_raises_on_empty_candles():
    assert _market_structure_evidence([], symbol="EURUSD", timeframe="M5") is None

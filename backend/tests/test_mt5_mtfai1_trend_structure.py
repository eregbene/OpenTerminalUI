"""mtfai1's own direction call is pure moving-average alignment (fast/slow SMA on M15, confirmed
by H1/H4 close-vs-average) -- it never looked at real swing-point structure. This adds a
lightweight confirmation via the platform's existing higher-high/higher-low (bullish) vs
lower-high/lower-low (bearish) classifier, without pulling in the full SMC analysis pipeline
mtfai1 was deliberately built to skip for performance.

Mocks classify_trend's return rather than constructing OHLC data that reliably triggers specific
fractal swing patterns (verified separately against real live M15 data during development --
GBPUSD showed a real bearish structure correctly blocking a LONG signal) -- these tests are about
_mtfai1_trend_structure_agrees' own decision logic (which states block which directions, the
fail-open behavior, the kill switch), not about re-testing detect_swings/classify_trend themselves
(covered by their own test files).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.mt5 import autonomous
from backend.market_structure.models import TrendLabel


def _bars(n: int = 60) -> list[dict]:
    return [{"time": f"2026-08-01T{(8 + i // 4):02d}:{(i % 4) * 15:02d}:00Z", "open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1} for i in range(n)]


@pytest.fixture(autouse=True)
def _enable_gate(monkeypatch):
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED", True)


def _patch_trend(monkeypatch, state: str):
    monkeypatch.setattr(autonomous, "classify_trend", lambda *a, **k: SimpleNamespace(state=state))
    monkeypatch.setattr(autonomous, "detect_swings", lambda *a, **k: [])


def test_bearish_structure_blocks_long(monkeypatch):
    _patch_trend(monkeypatch, TrendLabel.BEARISH.value)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "LONG", "GBPUSD") is False


def test_bearish_structure_allows_short(monkeypatch):
    _patch_trend(monkeypatch, TrendLabel.BEARISH.value)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "SHORT", "GBPUSD") is True


def test_bullish_structure_blocks_short(monkeypatch):
    _patch_trend(monkeypatch, TrendLabel.BULLISH.value)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "SHORT", "EURUSD") is False


def test_bullish_structure_allows_long(monkeypatch):
    _patch_trend(monkeypatch, TrendLabel.BULLISH.value)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "LONG", "EURUSD") is True


@pytest.mark.parametrize("state", [TrendLabel.RANGING.value, TrendLabel.TRANSITIONAL.value, TrendLabel.UNKNOWN.value])
def test_ambiguous_states_never_block_either_direction(monkeypatch, state):
    _patch_trend(monkeypatch, state)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "LONG", "EURUSD") is True
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "SHORT", "EURUSD") is True


def test_fails_open_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(autonomous, "normalize_bars", _boom)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "LONG", "EURUSD") is True
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "SHORT", "EURUSD") is True


def test_kill_switch_disables_check_entirely(monkeypatch):
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED", False)
    _patch_trend(monkeypatch, TrendLabel.BEARISH.value)
    # Would block LONG if the gate were active -- disabled, so it must not.
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "LONG", "GBPUSD") is True


def test_no_trade_direction_is_never_evaluated(monkeypatch):
    _patch_trend(monkeypatch, TrendLabel.BEARISH.value)
    assert autonomous._mtfai1_trend_structure_agrees(_bars(), "NO_TRADE", "EURUSD") is True


def _level(side: str, price: float, touch_count: int = 2):
    return SimpleNamespace(side=side, level=price, touch_count=touch_count)


@pytest.fixture(autouse=True)
def _enable_equal_level_gate(monkeypatch):
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED", True)


def _patch_levels(monkeypatch, levels):
    monkeypatch.setattr(autonomous, "detect_equal_levels", lambda *a, **k: levels)
    monkeypatch.setattr(autonomous, "detect_swings", lambda *a, **k: [])


def test_nearby_resistance_blocks_long(monkeypatch):
    _patch_levels(monkeypatch, [_level("buy_side", 1.1010)])  # 10 pips above entry
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")  # 0.5x ATR = 15 pips, level is 10 pips away -> inside band
    assert autonomous._mtfai1_equal_level_clear(_bars(), "LONG", "EURUSD", entry, atr) is False


def test_distant_resistance_does_not_block_long(monkeypatch):
    _patch_levels(monkeypatch, [_level("buy_side", 1.1100)])  # 100 pips above entry
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")
    assert autonomous._mtfai1_equal_level_clear(_bars(), "LONG", "EURUSD", entry, atr) is True


def test_nearby_support_blocks_short(monkeypatch):
    _patch_levels(monkeypatch, [_level("sell_side", 1.0990)])  # 10 pips below entry
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")
    assert autonomous._mtfai1_equal_level_clear(_bars(), "SHORT", "EURUSD", entry, atr) is False


def test_opposing_side_level_never_blocks(monkeypatch):
    # A sell_side (equal-low) level near a LONG's entry is behind it, not ahead -- irrelevant.
    _patch_levels(monkeypatch, [_level("sell_side", 1.0995)])
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")
    assert autonomous._mtfai1_equal_level_clear(_bars(), "LONG", "EURUSD", entry, atr) is True


def test_equal_level_kill_switch_disables_check(monkeypatch):
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED", False)
    _patch_levels(monkeypatch, [_level("buy_side", 1.1010)])
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")
    assert autonomous._mtfai1_equal_level_clear(_bars(), "LONG", "EURUSD", entry, atr) is True


def test_equal_level_fails_open_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(autonomous, "normalize_bars", _boom)
    entry = Decimal("1.1000")
    atr = Decimal("0.0030")
    assert autonomous._mtfai1_equal_level_clear(_bars(), "LONG", "EURUSD", entry, atr) is True


def test_score_candidate_forces_no_trade_when_structure_disagrees(monkeypatch):
    """End-to-end: _score_candidate's own SMA-based LONG call gets overridden to NO_TRADE when
    the structure layer disagrees."""
    monkeypatch.setattr(autonomous, "_mtfai1_trend_structure_agrees", lambda m15, direction, symbol: False)
    closes = [1.1000 + i * 0.0001 for i in range(60)]  # fast SMA > slow SMA -> would be LONG
    m15 = [SimpleNamespace(close=c, high=c + 0.0005, low=c - 0.0005) for c in closes]
    h1 = [SimpleNamespace(close=1.2000) for _ in range(25)]
    h4 = [SimpleNamespace(close=1.2000) for _ in range(25)]
    quote = SimpleNamespace(ask=1.1060, bid=1.1058, spread=0.0002)

    score, direction, geometry = autonomous._score_candidate(quote, m15, h1, h4, "EURUSD")

    assert direction == "NO_TRADE"

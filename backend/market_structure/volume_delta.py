"""Cumulative volume delta + bullish/bearish divergence detection -- independently implemented
from the PUBLIC, generic technical-analysis concept LuxAlgo's "Delta Tank" visualizes (cumulative
volume delta / CVD is standard, widely-published TA -- not exclusive to LuxAlgo; only their
specific visualization/branding is proprietary, which this does not reproduce). See
docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md's LuxAlgo kNN Market Architecture section.

Per-bar delta approximation: tick_volume * sign(close - open) -- MT5 M15 bars carry no real
bid/ask-side executed volume, so this is the standard proxy every public CVD implementation for
retail-broker OHLCV data uses (a bullish-bodied bar's volume is treated as buy-side pressure, a
bearish-bodied bar's as sell-side). Stated approximation, not a claim of true order-flow data.

THIS MODULE IS NOT YET WIRED INTO ANY STRATEGY OR LIVE DECISION.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from backend.market_structure.bar_utils import StructureBar

_DEFAULT_DIVERGENCE_LOOKBACK = 20


def bar_delta(bars: list[StructureBar]) -> list[float]:
    out: list[float] = []
    for b in bars:
        vol = float(b.volume) if b.volume is not None else 0.0
        sign = 1.0 if b.close > b.open else (-1.0 if b.close < b.open else 0.0)
        out.append(vol * sign)
    return out


def cumulative_delta(bars: list[StructureBar]) -> list[float]:
    """Running (never-reset) cumulative sum of bar_delta -- the CVD line."""
    deltas = bar_delta(bars)
    out: list[float] = []
    running = 0.0
    for d in deltas:
        running += d
        out.append(running)
    return out


class DivergenceState(NamedTuple):
    bullish: bool  # price lower low, CVD higher low
    bearish: bool  # price higher high, CVD lower high


def divergence_states(bars: list[StructureBar], cvd: list[float], lookback: int = _DEFAULT_DIVERGENCE_LOOKBACK) -> list[DivergenceState | None]:
    """Classic price-vs-CVD divergence: over a trailing `lookback` window, does price make a new
    extreme (high or low) that cumulative delta does NOT confirm (fails to make its own matching
    extreme)? None during warmup (fewer than `lookback` bars available)."""
    n = len(bars)
    out: list[DivergenceState | None] = []
    for i in range(n):
        if i + 1 < lookback:
            out.append(None)
            continue
        window_bars = bars[i + 1 - lookback : i + 1]
        window_cvd = cvd[i + 1 - lookback : i + 1]
        price_high_idx = max(range(len(window_bars)), key=lambda j: window_bars[j].high)
        price_low_idx = min(range(len(window_bars)), key=lambda j: window_bars[j].low)
        cvd_high_idx = max(range(len(window_cvd)), key=lambda j: window_cvd[j])
        cvd_low_idx = min(range(len(window_cvd)), key=lambda j: window_cvd[j])
        is_last = len(window_bars) - 1
        bearish = price_high_idx == is_last and cvd_high_idx != is_last and window_cvd[is_last] < window_cvd[cvd_high_idx]
        bullish = price_low_idx == is_last and cvd_low_idx != is_last and window_cvd[is_last] > window_cvd[cvd_low_idx]
        out.append(DivergenceState(bullish=bullish, bearish=bearish))
    return out

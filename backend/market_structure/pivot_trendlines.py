"""Dynamic pivot-trendline primitive built on top of swings.py's existing fractal pivots.

Deliberately does NOT call detect_swings() again -- StrategyContext.m15_snapshot.swings is
already the exact same SwingPoint list analyze_bars() produced for this cycle (Part 7's
"avoid a second trend-detection implementation" principle, applied here to swings the same
way context.py already applies it to trend/regime). This module's only job is the layer swings
themselves don't provide: connecting sequential same-type swings into a line, and describing
that line's slope, angle, live touch count, and break state.

Observability-only at introduction (2026-08-20 architecture blueprint, Section 3.2): computed
into StrategyContext, attached to candidate evidence, read by zero strategies until a
chronological OOS fingerprint replay proves the sign is stable -- the same path
squeeze_momentum was run through, which came back negative (see
mt5_strategies/families/_shared.py::_squeeze_evidence's docstring) and is exactly why this
module does not skip the observability step.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.models import SwingPoint

# A retracement/noise wiggle should never register as a "trendline" -- and a line steeper than
# this is no longer a usable projection (two swings one bar apart, effectively vertical). Angle
# is computed in ATR-normalized units (see _angle_degrees) so these bounds are comparable across
# instruments with very different price scales (XAUUSD ~4000 vs EURUSD ~1.1).
_MIN_ANGLE_DEGREES = 3.0
_MAX_ANGLE_DEGREES = 82.0
# "Active touch": price approaches within this many ATRs of the live-projected line without
# closing through it -- the exact tolerance specified for this module.
_TOUCH_TOLERANCE_ATR_MULT = 0.25
# How many of the most recent same-type swing pairs to turn into candidate lines per side
# (high/low) -- bounds output size; older pairs are superseded by more recent ones anyway.
_MAX_LINES_PER_SIDE = 3


@dataclass(frozen=True)
class TrendlinePivotSummary:
    """One candidate trendline connecting two sequential same-type swings. Line equation is
    `price = slope * bar_index + intercept`, evaluated against StrategyContext.m15_rows' own
    bar_index numbering (0-based, oldest first) so callers never need the raw swing objects."""

    id: str
    symbol: str
    timeframe: str
    swing_type: str  # "high" | "low" -- resistance (high) or support (low) line
    slope: float  # price change per bar
    intercept: float  # price at bar_index == 0
    start_bar_index: int
    end_bar_index: int  # the more recent of the two swings that define this line
    angle_degrees: float  # ATR-normalized, signed (positive = rising)
    touch_count: int  # bars after end_bar_index that approached without closing through
    is_broken: bool  # most recent bar's close is on the wrong side of the live line
    break_bar_index: int | None


def _line_value(slope: float, intercept: float, bar_index: int) -> float:
    return slope * bar_index + intercept


def _angle_degrees(slope: float, atr: float) -> float:
    """Slope normalized to "ATR per bar" units before taking the angle, so a EURUSD line and an
    XAUUSD line with visually-identical steepness produce comparable angles."""
    if atr <= 0:
        return 0.0
    return math.degrees(math.atan2(slope, atr))


def detect_pivot_trendlines(
    swings: list[SwingPoint],
    bars: list[StructureBar],
    atr_series: list[Decimal | None],
    *,
    symbol: str,
    timeframe: str,
) -> tuple[TrendlinePivotSummary, ...]:
    """Builds candidate trendlines from the most recent same-type swing pairs already present in
    `swings` (bar-index order, oldest first -- matches detect_swings()' own sort). `bars`/
    `atr_series` must be the same M15 bars/ATR series the swings were computed from (index-
    aligned), used only to measure touch count and break state going forward from each line.

    Returns an empty tuple (never raises) when there is not enough swing/bar history -- callers
    treat that identically to "no trendlines detected this cycle", not an error."""
    if len(bars) < 2 or not swings:
        return ()

    by_type: dict[str, list[SwingPoint]] = {"high": [], "low": []}
    for swing in sorted(swings, key=lambda s: s.bar_index):
        if swing.swing_type in by_type:
            by_type[swing.swing_type].append(swing)

    last_bar_index = bars[-1].index
    lines: list[TrendlinePivotSummary] = []
    for swing_type, points in by_type.items():
        if len(points) < 2:
            continue
        # Most recent MAX_LINES_PER_SIDE consecutive pairs, newest first.
        pairs = list(zip(points[-(_MAX_LINES_PER_SIDE + 1):-1], points[-_MAX_LINES_PER_SIDE:]))
        for start, end in pairs:
            bar_span = end.bar_index - start.bar_index
            if bar_span <= 0:
                continue
            price_start, price_end = float(start.price), float(end.price)
            slope = (price_end - price_start) / bar_span
            intercept = price_end - slope * end.bar_index
            leg_atr = atr_series[end.bar_index] if end.bar_index < len(atr_series) else None
            leg_atr_f = float(leg_atr) if leg_atr else 0.0
            angle = _angle_degrees(slope, leg_atr_f) if leg_atr_f > 0 else 0.0
            if abs(angle) < _MIN_ANGLE_DEGREES or abs(angle) > _MAX_ANGLE_DEGREES:
                continue

            touch_count = 0
            is_broken = False
            break_bar_index: int | None = None
            for bar in bars:
                if bar.index <= end.bar_index:
                    continue
                atr_here = atr_series[bar.index] if bar.index < len(atr_series) else None
                tolerance = float(atr_here) * _TOUCH_TOLERANCE_ATR_MULT if atr_here else 0.0
                line_price = _line_value(slope, intercept, bar.index)
                close = float(bar.close)
                if swing_type == "low":
                    # Support line: a touch approaches from above without closing below it.
                    if close < line_price - tolerance:
                        is_broken = True
                        break_bar_index = bar.index
                    elif abs(close - line_price) <= tolerance or float(bar.low) <= line_price <= float(bar.high):
                        touch_count += 1
                else:
                    # Resistance line: a touch approaches from below without closing above it.
                    if close > line_price + tolerance:
                        is_broken = True
                        break_bar_index = bar.index
                    elif abs(close - line_price) <= tolerance or float(bar.low) <= line_price <= float(bar.high):
                        touch_count += 1

            lines.append(
                TrendlinePivotSummary(
                    id=f"pvt_{symbol}_{timeframe}_{swing_type}_{start.bar_index}_{end.bar_index}",
                    symbol=symbol,
                    timeframe=timeframe,
                    swing_type=swing_type,
                    slope=slope,
                    intercept=intercept,
                    start_bar_index=start.bar_index,
                    end_bar_index=end.bar_index,
                    angle_degrees=angle,
                    touch_count=touch_count,
                    is_broken=is_broken,
                    break_bar_index=break_bar_index,
                )
            )
    return tuple(lines)

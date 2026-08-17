"""Bollinger-Band-vs-Keltner-Channel squeeze detection + linear-regression momentum histogram.

Independently implemented from the PUBLIC, well-documented methodology behind LazyBear's
"Squeeze Momentum Indicator" (itself a derivative of John Carter's TTM Squeeze, published in
"Mastering the Trade") -- no third-party source code was copied. See
docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md for the licensing/methodology research this is
based on.

Deliberately mirrors bar_utils.average_true_range's shape (Decimal in, `list[Decimal | None]`
out, one entry per input bar, None during warmup) rather than reusing core/technicals.py's
pandas-Series-based indicators -- this feature layer's own established convention (see
bar_utils.average_true_range's docstring precedent: a second, independent ATR implementation
exists here specifically so context.py has one Decimal-precise, StructureBar-native source,
not because pandas indicators are wrong elsewhere).

THIS MODULE IS NOT YET WIRED INTO ANY STRATEGY OR LIVE DECISION. It exists purely as a
feature-computation function for the squeeze/momentum research validation (standalone-strategy
backtest, confirmation-filter test, Historical Intelligence fingerprint test, combined test) --
see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md and the LAZYBEAR_SQUEEZE_VALIDATION research
log for that work. Adding it to StrategyContext makes it available to both the live strategy
engine and fingerprint generation from ONE computation, per the explicit instruction that any
promoted concept must be tested in both roles from a single shared feature -- but availability
in StrategyContext is not itself activation; no existing strategy reads these fields.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from backend.market_structure.bar_utils import StructureBar, average_true_range

SQUEEZE_ON = "ON"
SQUEEZE_OFF = "OFF"
SQUEEZE_RELEASING = "RELEASING"  # ON on the immediately preceding bar, OFF on this one

_DEFAULT_BB_PERIOD = 20
_DEFAULT_BB_MULT = Decimal("2.0")
_DEFAULT_KC_PERIOD = 20
_DEFAULT_KC_ATR_MULT = Decimal("1.5")
_DEFAULT_MOMENTUM_PERIOD = 20


class BandSeries(NamedTuple):
    upper: list[Decimal | None]
    mid: list[Decimal | None]
    lower: list[Decimal | None]


def _sma(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - period : i + 1], Decimal("0")) / Decimal(period))
    return out


def _ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = []
    k = Decimal(2) / Decimal(period + 1)
    prev: Decimal | None = None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(None)
            continue
        if prev is None:
            # Seed with an SMA over the first full window, matching the conventional EMA
            # initialization every other EMA in this codebase already uses.
            prev = sum(values[i + 1 - period : i + 1], Decimal("0")) / Decimal(period)
        else:
            prev = v * k + prev * (Decimal(1) - k)
        out.append(prev)
    return out


def _stdev(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
            continue
        window = values[i + 1 - period : i + 1]
        mean = sum(window, Decimal("0")) / Decimal(period)
        variance = sum((v - mean) ** 2 for v in window) / Decimal(period)
        out.append(variance.sqrt())
    return out


def bollinger_bands(bars: list[StructureBar], period: int = _DEFAULT_BB_PERIOD, mult: Decimal = _DEFAULT_BB_MULT) -> BandSeries:
    closes = [b.close for b in bars]
    mid = _sma(closes, period)
    stdev = _stdev(closes, period)
    upper = [m + mult * s if m is not None and s is not None else None for m, s in zip(mid, stdev)]
    lower = [m - mult * s if m is not None and s is not None else None for m, s in zip(mid, stdev)]
    return BandSeries(upper=upper, mid=mid, lower=lower)


def keltner_channels(bars: list[StructureBar], period: int = _DEFAULT_KC_PERIOD, atr_mult: Decimal = _DEFAULT_KC_ATR_MULT) -> BandSeries:
    closes = [b.close for b in bars]
    mid = _ema(closes, period)
    atrs = average_true_range(bars, period)
    upper = [m + atr_mult * a if m is not None and a is not None else None for m, a in zip(mid, atrs)]
    lower = [m - atr_mult * a if m is not None and a is not None else None for m, a in zip(mid, atrs)]
    return BandSeries(upper=upper, mid=mid, lower=lower)


def squeeze_states(bb: BandSeries, kc: BandSeries) -> list[str | None]:
    """ON when Bollinger Bands sit fully inside Keltner Channels (the classic TTM/LazyBear
    compression condition); RELEASING marks the exact bar where a prior ON transitions to OFF
    (the moment LazyBear's indicator flags with a color change) -- everything else is OFF.
    None only during the shared warmup window where either band series isn't computable yet."""
    out: list[str | None] = []
    prev_on: bool | None = None
    for bu, bl, ku, kl in zip(bb.upper, bb.lower, kc.upper, kc.lower):
        if bu is None or bl is None or ku is None or kl is None:
            out.append(None)
            prev_on = None
            continue
        is_on = bu < ku and bl > kl
        if is_on:
            out.append(SQUEEZE_ON)
        elif prev_on:
            out.append(SQUEEZE_RELEASING)
        else:
            out.append(SQUEEZE_OFF)
        prev_on = is_on
    return out


def _linreg_endpoint(window: list[float]) -> float:
    """Value of the least-squares line fit through `window` (x = 0..len-1), evaluated at the
    most recent point (x = len-1) -- equivalent to Pine's linreg(source, length, 0)."""
    n = len(window)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(window) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return mean_y
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    return slope * (n - 1) + intercept


def squeeze_momentum(bars: list[StructureBar], period: int = _DEFAULT_MOMENTUM_PERIOD) -> list[float | None]:
    """LazyBear's own momentum formula (linreg-based, explicitly NOT Carter's original Donchian-
    midline momentum -- see the indicator's own description): for each bar, source = close -
    avg(avg(highest_high_N, lowest_low_N), sma_close_N); the histogram value is a rolling
    linear-regression fit of that source series, evaluated at each bar's own endpoint."""
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    closes = [float(b.close) for b in bars]
    n = len(bars)
    source: list[float | None] = []
    for i in range(n):
        if i + 1 < period:
            source.append(None)
            continue
        window_high = highs[i + 1 - period : i + 1]
        window_low = lows[i + 1 - period : i + 1]
        window_close = closes[i + 1 - period : i + 1]
        donchian_mid = (max(window_high) + min(window_low)) / 2.0
        sma_close = sum(window_close) / period
        avg_ref = (donchian_mid + sma_close) / 2.0
        source.append(closes[i] - avg_ref)

    out: list[float | None] = []
    for i in range(n):
        if i + 1 < period or any(v is None for v in source[i + 1 - period : i + 1]):
            out.append(None)
            continue
        window = source[i + 1 - period : i + 1]
        out.append(_linreg_endpoint(window))  # type: ignore[arg-type]
    return out

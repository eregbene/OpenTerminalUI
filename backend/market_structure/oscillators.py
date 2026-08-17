"""RSI / WaveTrend / CCI / ADX oscillators + rolling min-max normalization -- the exact feature
set jdehorty's public "Machine Learning: Lorentzian Classification" methodology description uses
(confirmed absent from this repo entirely before now: no ADX, no CCI, no Wave Trend anywhere; RSI
existed only as core/technicals.py's simple, non-Wilder variant). Independently implemented from
the well-documented public formulas -- no third-party source was copied; jdehorty's own reference
implementation is MPL 2.0 licensed (see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md) but this
module was written from the standard technical-analysis definitions, not ported from it.

Mirrors squeeze_momentum.py's convention: StructureBar in, plain `list[float | None]` out (one
entry per bar, None during warmup), float math internally (these are dimensionless oscillators,
not price levels, so Decimal precision buys nothing here -- same reasoning squeeze_momentum.py's
own linreg histogram already uses for its momentum series).

THIS MODULE IS NOT YET WIRED INTO ANY STRATEGY OR LIVE DECISION -- see
backend/historical_intelligence/lorentzian_similarity.py for the kNN/distance engine built on top
of these features, and the LORENTZIAN_VALIDATION research stage for the four-role test harness.
"""
from __future__ import annotations

from backend.market_structure.bar_utils import StructureBar

_DEFAULT_RSI_PERIOD = 14
_DEFAULT_CCI_PERIOD = 20
_DEFAULT_ADX_PERIOD = 14
_DEFAULT_WT_CHANNEL_PERIOD = 10
_DEFAULT_WT_AVERAGE_PERIOD = 21
_DEFAULT_WT_SIGNAL_PERIOD = 4
_DEFAULT_NORMALIZE_WINDOW = 200


def _ema(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2.0 / (period + 1.0)
    prev: float | None = None
    for v in values:
        if v is None:
            out.append(None)
            prev = None
            continue
        prev = v if prev is None else v * k + prev * (1.0 - k)
        out.append(prev)
    return out


def _sma(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = []
    window: list[float] = []
    for v in values:
        if v is None:
            out.append(None)
            window = []
            continue
        window.append(v)
        if len(window) > period:
            window.pop(0)
        out.append(sum(window) / len(window) if len(window) == period else None)
    return out


def rsi(bars: list[StructureBar], period: int = _DEFAULT_RSI_PERIOD) -> list[float | None]:
    """Wilder-smoothed RSI (the standard definition jdehorty's methodology description uses --
    deliberately NOT core/technicals.py's simple/non-Wilder RSI, a second independent RSI
    implementation existing here on purpose, same precedent as this engine's own second ATR
    implementation in bar_utils.average_true_range)."""
    closes = [float(b.close) for b in bars]
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def cci(bars: list[StructureBar], period: int = _DEFAULT_CCI_PERIOD) -> list[float | None]:
    """Standard Commodity Channel Index: (typical_price - SMA(tp, N)) / (0.015 * mean_abs_dev)."""
    tp = [float(b.high + b.low + b.close) / 3.0 for b in bars]
    out: list[float | None] = []
    for i in range(len(tp)):
        if i + 1 < period:
            out.append(None)
            continue
        window = tp[i + 1 - period : i + 1]
        mean = sum(window) / period
        mad = sum(abs(v - mean) for v in window) / period
        out.append(0.0 if mad == 0 else (tp[i] - mean) / (0.015 * mad))
    return out


def wave_trend(
    bars: list[StructureBar],
    channel_period: int = _DEFAULT_WT_CHANNEL_PERIOD,
    average_period: int = _DEFAULT_WT_AVERAGE_PERIOD,
) -> list[float | None]:
    """LazyBear's public WaveTrend Oscillator formula (WT1 only -- the single-value feature
    jdehorty's methodology description uses, not the WT1/WT2 crossover signal): ap = hlc3;
    esa = ema(ap, channel_period); d = ema(|ap - esa|, channel_period);
    ci = (ap - esa) / (0.015 * d); wt1 = ema(ci, average_period)."""
    ap = [float(b.high + b.low + b.close) / 3.0 for b in bars]
    esa = _ema(ap, channel_period)  # type: ignore[arg-type]
    abs_dev: list[float | None] = [abs(a - e) if e is not None else None for a, e in zip(ap, esa)]
    d = _ema(abs_dev, channel_period)
    ci: list[float | None] = []
    for a, e, dv in zip(ap, esa, d):
        if e is None or dv is None or dv == 0:
            ci.append(None)
        else:
            ci.append((a - e) / (0.015 * dv))
    return _ema(ci, average_period)


def adx(bars: list[StructureBar], period: int = _DEFAULT_ADX_PERIOD) -> list[float | None]:
    """Standard Wilder-smoothed Average Directional Index."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period * 2 + 1:
        return out
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = float(bars[i].high - bars[i - 1].high)
        down = float(bars[i - 1].low - bars[i].low)
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(
            float(bars[i].high - bars[i].low),
            abs(float(bars[i].high) - float(bars[i - 1].close)),
            abs(float(bars[i].low) - float(bars[i - 1].close)),
        )

    def _wilder_smooth(values: list[float], period: int) -> list[float | None]:
        smoothed: list[float | None] = [None] * len(values)
        if len(values) <= period:
            return smoothed
        first = sum(values[1 : period + 1])
        smoothed[period] = first
        for i in range(period + 1, len(values)):
            smoothed[i] = smoothed[i - 1] - (smoothed[i - 1] / period) + values[i]
        return smoothed

    tr_s = _wilder_smooth(tr, period)
    plus_dm_s = _wilder_smooth(plus_dm, period)
    minus_dm_s = _wilder_smooth(minus_dm, period)

    dx: list[float | None] = [None] * n
    for i in range(period, n):
        if tr_s[i] in (None, 0):
            continue
        plus_di = 100.0 * (plus_dm_s[i] / tr_s[i])
        minus_di = 100.0 * (minus_dm_s[i] / tr_s[i])
        denom = plus_di + minus_di
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom

    dx_values = [v for v in dx if v is not None]
    if len(dx_values) < period:
        return out
    first_valid = next(i for i, v in enumerate(dx) if v is not None)
    adx_start = first_valid + period - 1
    if adx_start >= n:
        return out
    out[adx_start] = sum(v for v in dx[first_valid : adx_start + 1] if v is not None) / period
    for i in range(adx_start + 1, n):
        if dx[i] is None or out[i - 1] is None:
            continue
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def rolling_minmax_normalize(values: list[float | None], window: int = _DEFAULT_NORMALIZE_WINDOW) -> list[float | None]:
    """Causal rolling min-max scale to [0, 1] over the trailing `window` bars -- brings
    differently-scaled oscillators (RSI/ADX naturally 0-100, CCI/WaveTrend effectively unbounded)
    onto a comparable range before Lorentzian distance sums them, matching jdehorty's own public
    methodology description (features are normalized before distance computation, not compared
    raw). Never uses a value not yet seen as of index i -- the window is values[max(0,i-window+1):i+1]."""
    out: list[float | None] = []
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        window_vals = [x for x in values[max(0, i - window + 1) : i + 1] if x is not None]
        lo, hi = min(window_vals), max(window_vals)
        out.append(0.5 if hi == lo else (v - lo) / (hi - lo))
    return out


def lorentzian_feature_series(bars: list[StructureBar], *, normalize_window: int = _DEFAULT_NORMALIZE_WINDOW) -> list[tuple[float, float, float, float] | None]:
    """The 4-feature vector series (RSI, WaveTrend, CCI, ADX -- jdehorty's default feature set),
    each independently rolling-normalized to [0, 1]. One entry per bar; None until all four
    features have cleared their own warmup AND the normalization window has at least one prior
    valid point."""
    rsi_s = rolling_minmax_normalize(rsi(bars), normalize_window)
    wt_s = rolling_minmax_normalize(wave_trend(bars), normalize_window)
    cci_s = rolling_minmax_normalize(cci(bars), normalize_window)
    adx_s = rolling_minmax_normalize(adx(bars), normalize_window)
    out: list[tuple[float, float, float, float] | None] = []
    for r, w, c, a in zip(rsi_s, wt_s, cci_s, adx_s):
        out.append((r, w, c, a) if None not in (r, w, c, a) else None)
    return out

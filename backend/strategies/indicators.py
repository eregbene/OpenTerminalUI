from __future__ import annotations

from math import sqrt
from statistics import pstdev
from typing import Any


def _closes(bars: list[dict[str, Any]]) -> list[float]:
    return [float(row["close"]) for row in bars]


def _highs(bars: list[dict[str, Any]]) -> list[float]:
    return [float(row["high"]) for row in bars]


def _lows(bars: list[dict[str, Any]]) -> list[float]:
    return [float(row["low"]) for row in bars]


def _volumes(bars: list[dict[str, Any]]) -> list[float]:
    return [float(row.get("volume") or 0.0) for row in bars]


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)
    alpha = 2.0 / (period + 1.0)
    out: list[float | None] = [None] * len(values)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for idx in range(period, len(values)):
        current = (values[idx] * alpha) + (current * (1 - alpha))
        out[idx] = current
    return out


def ema(values: list[float], period: int) -> float | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def true_ranges(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    prev_close: float | None = None
    for row in bars:
        high = float(row["high"])
        low = float(row["low"])
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        out.append(tr)
        prev_close = float(row["close"])
    return out


def atr(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    return sma(true_ranges(bars), period)


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(len(values) - period, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float | None]:
    fast_series = ema_series(values, fast)
    slow_series = ema_series(values, slow)
    macd_line: list[float] = []
    for fast_value, slow_value in zip(fast_series, slow_series):
        if fast_value is not None and slow_value is not None:
            macd_line.append(fast_value - slow_value)
    signal_line = ema(macd_line, signal) if macd_line else None
    line = macd_line[-1] if macd_line else None
    return {"line": line, "signal": signal_line, "histogram": (line - signal_line) if line is not None and signal_line is not None else None}


def bollinger(values: list[float], period: int = 20, deviations: float = 2.0) -> dict[str, float | None]:
    if len(values) < period:
        return {"middle": None, "upper": None, "lower": None}
    window = values[-period:]
    middle = sum(window) / period
    sd = pstdev(window)
    return {"middle": middle, "upper": middle + deviations * sd, "lower": middle - deviations * sd}


def stochastic(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(bars) < period:
        return None
    high = max(_highs(bars)[-period:])
    low = min(_lows(bars)[-period:])
    if high == low:
        return None
    return 100.0 * ((float(bars[-1]["close"]) - low) / (high - low))


def adx(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    ranges = true_ranges(bars)
    if len(ranges) < period:
        return None
    avg_range = sum(ranges[-period:]) / period
    close = _closes(bars)
    directional = abs(close[-1] - close[-period]) / period
    return min(100.0, 100.0 * directional / avg_range) if avg_range else None


def roc(values: list[float], period: int = 12) -> float | None:
    if len(values) <= period or values[-period - 1] == 0:
        return None
    return (values[-1] - values[-period - 1]) / values[-period - 1]


def historical_volatility(values: list[float], period: int = 20) -> float | None:
    if len(values) <= period:
        return None
    returns = []
    for idx in range(len(values) - period + 1, len(values)):
        prev = values[idx - 1]
        if prev:
            returns.append((values[idx] - prev) / prev)
    return pstdev(returns) * sqrt(252) if len(returns) >= 2 else None


def compute_indicators(bars: list[dict[str, Any]]) -> dict[str, Any]:
    close = _closes(bars)
    volume = _volumes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    return {
        "indicator.sma.20": sma(close, 20),
        "indicator.sma.50": sma(close, 50),
        "indicator.ema.20": ema(close, 20),
        "indicator.ema.50": ema(close, 50),
        "indicator.rsi.14": rsi(close, 14),
        "indicator.atr.14": atr(bars, 14),
        "indicator.macd.histogram": macd(close)["histogram"],
        "indicator.bollinger.upper": bollinger(close)["upper"],
        "indicator.bollinger.lower": bollinger(close)["lower"],
        "indicator.stochastic.14": stochastic(bars, 14),
        "indicator.adx.14": adx(bars, 14),
        "indicator.roc.12": roc(close, 12),
        "indicator.volume.sma.20": sma(volume, 20),
        "indicator.historical_volatility.20": historical_volatility(close, 20),
        "indicator.donchian.high.20": max(highs[-21:-1], default=None) if len(highs) > 20 else None,
        "indicator.donchian.low.20": min(lows[-21:-1], default=None) if len(lows) > 20 else None,
    }

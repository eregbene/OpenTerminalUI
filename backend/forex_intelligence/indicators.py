from __future__ import annotations

import math
from statistics import mean, pstdev

from backend.market_structure.bar_utils import StructureBar


def _f(value: object) -> float:
    return float(value)


def ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out: list[float | None] = []
    current: float | None = None
    for idx, value in enumerate(values):
        if idx + 1 < period:
            out.append(None)
            continue
        if current is None:
            current = mean(values[idx + 1 - period : idx + 1])
        else:
            current = value * alpha + current * (1 - alpha)
        out.append(current)
    return out


def atr_values(bars: list[StructureBar], period: int = 14) -> list[float | None]:
    trs: list[float] = []
    out: list[float | None] = []
    prev_close: float | None = None
    for bar in bars:
        high = _f(bar.high)
        low = _f(bar.low)
        tr = max(high - low, abs(high - prev_close) if prev_close is not None else 0.0, abs(low - prev_close) if prev_close is not None else 0.0)
        trs.append(tr)
        out.append(mean(trs[-period:]) if len(trs) >= period else None)
        prev_close = _f(bar.close)
    return out


def rsi_values(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None]
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
        if idx < period:
            out.append(None)
            continue
        avg_gain = mean(gains[-period:])
        avg_loss = mean(losses[-period:])
        out.append(100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss)))
    return out


def stochastic_rsi(rsi: list[float | None], period: int = 14) -> float | None:
    window = [value for value in rsi[-period:] if value is not None]
    current = rsi[-1] if rsi else None
    if current is None or len(window) < 2:
        return None
    low = min(window)
    high = max(window)
    return 0.5 if high == low else (current - low) / (high - low)


def macd(values: list[float]) -> tuple[float | None, float | None]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    line = [None if f is None or s is None else f - s for f, s in zip(fast, slow)]
    valid = [value for value in line if value is not None]
    signal_values = ema(valid, 9)
    return line[-1], signal_values[-1] if signal_values else None


def roc(values: list[float], period: int = 12) -> float | None:
    if len(values) <= period or values[-period - 1] == 0:
        return None
    return (values[-1] - values[-period - 1]) / values[-period - 1]


def historical_volatility(values: list[float], period: int = 20) -> float | None:
    if len(values) <= period:
        return None
    returns = [math.log(values[idx] / values[idx - 1]) for idx in range(len(values) - period, len(values)) if values[idx - 1] > 0 and values[idx] > 0]
    if len(returns) < 2:
        return None
    return pstdev(returns) * math.sqrt(252)


def regression_slope(values: list[float], period: int = 20) -> float | None:
    if len(values) < period:
        return None
    y = values[-period:]
    x = list(range(period))
    x_mean = mean(x)
    y_mean = mean(y)
    denom = sum((item - x_mean) ** 2 for item in x)
    if denom == 0:
        return None
    return sum((xv - x_mean) * (yv - y_mean) for xv, yv in zip(x, y)) / denom


def adx_proxy(bars: list[StructureBar], period: int = 14) -> float | None:
    if len(bars) <= period:
        return None
    closes = [_f(bar.close) for bar in bars[-period:]]
    ranges = [_f(bar.high) - _f(bar.low) for bar in bars[-period:]]
    avg_range = mean(ranges)
    if avg_range == 0:
        return 0.0
    return min(100.0, abs(closes[-1] - closes[0]) / avg_range / period * 100)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean
from typing import Any

from backend.forex_intelligence.indicators import atr_values, ema, macd, rsi_values, stochastic_rsi
from backend.market_structure.bar_utils import StructureBar


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    timeframe: str
    session: str
    timestamp: datetime
    spread: float | None
    volatility: float | None
    atr: float | None
    adr: float | None
    liquidity_score: float
    ema20: float | None
    ema50: float | None
    ema200: float | None
    ema20_slope: float | None
    ema50_slope: float | None
    ema200_slope: float | None
    higher_timeframe_trend: str
    trend_strength: float
    market_structure: dict[str, Any]
    momentum: dict[str, Any]
    volatility_state: dict[str, Any]
    support_resistance: dict[str, Any]
    sessions: dict[str, bool]
    market_regime: str
    news_risk: dict[str, Any] = field(default_factory=lambda: {"status": "placeholder", "risk": "unknown"})

    def model_dump(self) -> dict[str, Any]:
        return {
            "market": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "session": self.session,
                "timestamp": self.timestamp.isoformat(),
                "spread": self.spread,
                "volatility": self.volatility,
                "atr": self.atr,
                "adr": self.adr,
                "liquidity_score": self.liquidity_score,
            },
            "trend": {
                "ema20": self.ema20,
                "ema50": self.ema50,
                "ema200": self.ema200,
                "ema_slopes": {"ema20": self.ema20_slope, "ema50": self.ema50_slope, "ema200": self.ema200_slope},
                "higher_timeframe_trend": self.higher_timeframe_trend,
                "trend_strength": self.trend_strength,
            },
            "market_structure": self.market_structure,
            "momentum": self.momentum,
            "volatility": self.volatility_state,
            "support_resistance": self.support_resistance,
            "sessions": self.sessions,
            "market_regime": self.market_regime,
            "news_risk": self.news_risk,
        }


def build_market_context(
    candles: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    spread: float | None = None,
    higher_timeframe_trend: str = "unknown",
) -> MarketContext:
    bars = [_bar(row) for row in candles]
    closes = [float(row["c"]) for row in candles]
    highs = [float(row["h"]) for row in candles]
    lows = [float(row["l"]) for row in candles]
    volumes = [float(row.get("v") or 0) for row in candles]
    timestamp = datetime.fromtimestamp(int(candles[-1]["t"]), tz=timezone.utc)
    ema20_values = ema(closes, 20)
    ema50_values = ema(closes, 50)
    ema200_values = ema(closes, 200)
    atr_series = atr_values(bars, 14)
    rsi_series = rsi_values(closes, 14)
    macd_line, macd_signal = macd(closes)
    sr = _support_resistance(candles)
    ms = _market_structure(candles)
    atr = atr_series[-1] if atr_series else None
    adr = mean([highs[idx] - lows[idx] for idx in range(max(0, len(highs) - min(20, len(highs))), len(highs))]) if highs else None
    volatility = _volatility(closes)
    trend_strength = _trend_strength(closes, ema20_values[-1] if ema20_values else None, ema50_values[-1] if ema50_values else None)
    regime = _regime(trend_strength=trend_strength, atr_percentile=_percentile_rank([v for v in atr_series if v is not None], atr), structure=ms)
    return MarketContext(
        symbol=symbol,
        timeframe=timeframe,
        session=_session(timestamp),
        timestamp=timestamp,
        spread=spread,
        volatility=volatility,
        atr=atr,
        adr=adr,
        liquidity_score=_liquidity_score(volumes, spread),
        ema20=ema20_values[-1] if ema20_values else None,
        ema50=ema50_values[-1] if ema50_values else None,
        ema200=ema200_values[-1] if ema200_values else None,
        ema20_slope=_slope(ema20_values),
        ema50_slope=_slope(ema50_values),
        ema200_slope=_slope(ema200_values),
        higher_timeframe_trend=higher_timeframe_trend,
        trend_strength=trend_strength,
        market_structure=ms,
        momentum={"rsi": rsi_series[-1] if rsi_series else None, "stochastic_rsi": stochastic_rsi(rsi_series), "macd": macd_line, "macd_signal": macd_signal, "mfi": _mfi(candles)},
        volatility_state={
            "atr_percentile": _percentile_rank([v for v in atr_series if v is not None], atr),
            "bollinger_width": _bollinger_width(closes),
            "compression": regime == "LOW_VOLATILITY",
            "expansion": regime in {"BREAKOUT", "HIGH_VOLATILITY"},
        },
        support_resistance=sr,
        sessions=_sessions(timestamp),
        market_regime=regime,
    )


def _bar(row: dict[str, Any]) -> StructureBar:
    open_time = datetime.fromtimestamp(int(row["t"]), tz=timezone.utc)
    return StructureBar(
        index=0,
        symbol="",
        timeframe="",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=Decimal(str(row["o"])),
        high=Decimal(str(row["h"])),
        low=Decimal(str(row["l"])),
        close=Decimal(str(row["c"])),
        volume=Decimal(str(row.get("v") or 0)),
    )


def _slope(values: list[float | None], lookback: int = 5) -> float | None:
    valid = [value for value in values if value is not None]
    if len(valid) <= lookback:
        return None
    return valid[-1] - valid[-1 - lookback]


def _support_resistance(candles: list[dict[str, Any]]) -> dict[str, Any]:
    current = candles[-1]
    prior_candles = candles[:-1] or candles
    window = prior_candles[-60:]
    daily = prior_candles[-96:] if len(prior_candles) >= 96 else window
    previous = prior_candles[-192:-96] if len(prior_candles) >= 192 else window[:-len(window) // 2] or window
    close = float(candles[-1]["c"])
    supports = sorted([float(row["l"]) for row in window if float(row["l"]) <= close], reverse=True)
    resistances = sorted([float(row["h"]) for row in window if float(row["h"]) >= close])
    return {
        "current_price": close,
        "current_open": float(current["o"]),
        "current_high": float(current["h"]),
        "current_low": float(current["l"]),
        "current_close": close,
        "nearest_support": supports[0] if supports else min(float(row["l"]) for row in window),
        "nearest_resistance": resistances[0] if resistances else max(float(row["h"]) for row in window),
        "daily_high": max(float(row["h"]) for row in daily),
        "daily_low": min(float(row["l"]) for row in daily),
        "previous_session_high": max(float(row["h"]) for row in previous),
        "previous_session_low": min(float(row["l"]) for row in previous),
    }


def _market_structure(candles: list[dict[str, Any]]) -> dict[str, Any]:
    highs = [float(row["h"]) for row in candles[-12:]]
    lows = [float(row["l"]) for row in candles[-12:]]
    hh = len(highs) >= 2 and highs[-1] > max(highs[:-1])
    ll = len(lows) >= 2 and lows[-1] < min(lows[:-1])
    hl = len(lows) >= 4 and lows[-1] > min(lows[-4:-1])
    lh = len(highs) >= 4 and highs[-1] < max(highs[-4:-1])
    bos = "bullish" if hh and hl else ("bearish" if ll and lh else None)
    choch = "bullish" if hh and not hl else ("bearish" if ll and not lh else None)
    return {"HH": hh, "HL": hl, "LH": lh, "LL": ll, "BOS": bos, "CHOCH": choch}


def _volatility(closes: list[float]) -> float | None:
    if len(closes) < 20:
        return None
    returns = [abs((closes[idx] - closes[idx - 1]) / closes[idx - 1]) for idx in range(len(closes) - 19, len(closes)) if closes[idx - 1]]
    return mean(returns) if returns else None


def _bollinger_width(closes: list[float], period: int = 20) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    avg = mean(window)
    variance = mean([(value - avg) ** 2 for value in window])
    stdev = variance ** 0.5
    return (4 * stdev / avg) if avg else None


def _percentile_rank(values: list[float], current: float | None) -> float | None:
    if current is None or not values:
        return None
    return len([value for value in values if value <= current]) / len(values)


def _liquidity_score(volumes: list[float], spread: float | None) -> float:
    volume_score = 0.5
    if len(volumes) > 20 and mean(volumes[-20:]) > 0:
        volume_score = min(1.0, volumes[-1] / mean(volumes[-20:]))
    spread_penalty = min(0.5, float(spread or 0) / 10)
    return max(0.0, min(1.0, volume_score - spread_penalty))


def _trend_strength(closes: list[float], ema20: float | None, ema50: float | None) -> float:
    if ema20 is None or ema50 is None or not closes:
        return 0.0
    distance = abs(ema20 - ema50) / closes[-1]
    direction = 1 if (closes[-1] > ema20 > ema50 or closes[-1] < ema20 < ema50) else 0.5
    return min(1.0, distance * 1000) * direction


def _regime(*, trend_strength: float, atr_percentile: float | None, structure: dict[str, Any]) -> str:
    if atr_percentile is not None and atr_percentile >= 0.85:
        return "HIGH_VOLATILITY"
    if atr_percentile is not None and atr_percentile <= 0.2:
        return "LOW_VOLATILITY"
    if structure.get("BOS"):
        return "BREAKOUT"
    if trend_strength >= 0.65:
        return "TRENDING"
    if trend_strength >= 0.35:
        return "PULLBACK"
    return "RANGING"


def _mfi(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    positive = 0.0
    negative = 0.0
    for idx in range(len(candles) - period, len(candles)):
        current = candles[idx]
        previous = candles[idx - 1]
        typical = (float(current["h"]) + float(current["l"]) + float(current["c"])) / 3
        previous_typical = (float(previous["h"]) + float(previous["l"]) + float(previous["c"])) / 3
        flow = typical * float(current.get("v") or 0)
        if typical >= previous_typical:
            positive += flow
        else:
            negative += flow
    if negative == 0:
        return 100.0
    ratio = positive / negative
    return 100 - (100 / (1 + ratio))


def _session(timestamp: datetime) -> str:
    hour = timestamp.hour
    if 0 <= hour < 7:
        return "Asian"
    if 7 <= hour < 12:
        return "London"
    if 12 <= hour < 16:
        return "Overlap"
    if 16 <= hour < 21:
        return "New York"
    return "Asian"


def _sessions(timestamp: datetime) -> dict[str, bool]:
    name = _session(timestamp)
    return {"Asian": name == "Asian", "London": name == "London", "New_York": name == "New York", "Overlap": name == "Overlap"}

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from backend.adaptive_management.tp_protection import construct_dynamic_stop
from backend.intelligence.trading.market_context import MarketContext


@dataclass(frozen=True)
class StrategyOutput:
    strategy: str
    decision: str
    confidence: float
    risk_reward: float
    entry: float | None
    stop: float | None
    target: float | None
    reason: str
    valid: bool = True
    strategy_name: str | None = None
    direction: str | None = None
    eligible: bool = False
    entry_conditions_met: bool = False
    rejection_codes: list[str] | None = None
    rejection_details: dict[str, Any] | None = None
    required_inputs: list[str] | None = None
    missing_inputs: list[str] | None = None
    stale_inputs: list[str] | None = None
    indicator_values: dict[str, Any] | None = None
    thresholds_used: dict[str, Any] | None = None
    calculated_risk_reward: float | None = None
    market_regime_compatibility: str = "UNKNOWN"
    higher_timeframe_compatibility: str = "UNKNOWN"
    conflict_classification: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None

    def model_dump(self) -> dict[str, object]:
        payload = self.__dict__.copy()
        payload["strategy_name"] = payload["strategy_name"] or self.strategy
        payload["direction"] = payload["direction"] or self.decision
        payload["rejection_codes"] = payload["rejection_codes"] or []
        payload["rejection_details"] = payload["rejection_details"] or {}
        payload["required_inputs"] = payload["required_inputs"] or []
        payload["missing_inputs"] = payload["missing_inputs"] or []
        payload["stale_inputs"] = payload["stale_inputs"] or []
        payload["indicator_values"] = payload["indicator_values"] or {}
        payload["thresholds_used"] = payload["thresholds_used"] or {}
        payload["conflict_classification"] = payload["conflict_classification"] or {}
        payload["geometry"] = payload["geometry"] or {}
        return payload


class DeterministicStrategy(Protocol):
    name: str

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        ...


class EMATrendStrategy:
    name = "EMA Trend"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        required = ["price", "ema20", "ema50", "ema200", "ema20_slope", "ema200_slope"]
        missing = _missing({"price": price, "ema20": context.ema20, "ema50": context.ema50, "ema200": context.ema200, "ema20_slope": context.ema20_slope, "ema200_slope": context.ema200_slope})
        if not missing:
            if price > context.ema20 > context.ema50 > context.ema200 and (context.ema20_slope or 0) > 0 and (context.ema200_slope or 0) >= 0:
                return _trade(self.name, "LONG", context, 0.72, "EMA stack and slope bullish", required_inputs=required)
            if price < context.ema20 < context.ema50 < context.ema200 and (context.ema20_slope or 0) < 0 and (context.ema200_slope or 0) <= 0:
                return _trade(self.name, "SHORT", context, 0.72, "EMA stack and slope bearish", required_inputs=required)
        codes = ["EMA200_WARMUP_UNAVAILABLE" if "ema200" in missing else "REQUIRED_DATA_MISSING"] if missing else ["EMA_ALIGNMENT_FAILED", "EMA_SLOPE_TOO_WEAK"]
        return _no_trade(self.name, "EMA stack is not aligned", codes, required_inputs=required, missing_inputs=missing, context=context)


class BreakoutStrategy:
    name = "Breakout"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        sr = context.support_resistance
        required = ["price", "BOS", "nearest_resistance", "nearest_support"]
        if price and context.market_structure.get("BOS") == "bullish" and price >= float(sr["nearest_resistance"]):
            return _trade(self.name, "LONG", context, 0.7, "Bullish break of resistance", required_inputs=required)
        if price and context.market_structure.get("BOS") == "bearish" and price <= float(sr["nearest_support"]):
            return _trade(self.name, "SHORT", context, 0.7, "Bearish break of support", required_inputs=required)
        codes = ["NO_BOS"] if not context.market_structure.get("BOS") else ["BREAKOUT_NOT_CONFIRMED"]
        return _no_trade(self.name, "No validated breakout", codes, required_inputs=required, context=context)


class PullbackStrategy:
    name = "Pullback"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        required = ["price", "ema20", "ema50"]
        missing = _missing({"price": price, "ema20": context.ema20, "ema50": context.ema50})
        tolerance = max(float(context.atr or 0) * 0.25, 0.00025)
        local_bullish = bool(context.ema20 and context.ema50 and context.ema20 >= context.ema50)
        local_bearish = bool(context.ema20 and context.ema50 and context.ema20 <= context.ema50)
        htf_long = classify_higher_timeframe("LONG", context.higher_timeframe_trend)
        htf_short = classify_higher_timeframe("SHORT", context.higher_timeframe_trend)
        if not missing and local_bullish and context.ema50 - tolerance <= price <= context.ema20 + tolerance and htf_long != "CONFLICT":
            return _trade(self.name, "LONG", context, 0.66, "Bullish pullback into EMA zone", required_inputs=required)
        if not missing and local_bearish and context.ema20 - tolerance <= price <= context.ema50 + tolerance and htf_short != "CONFLICT":
            return _trade(self.name, "SHORT", context, 0.66, "Bearish pullback into EMA zone", required_inputs=required)
        if missing:
            codes = ["REQUIRED_DATA_MISSING"]
        elif htf_long == "CONFLICT" and local_bullish or htf_short == "CONFLICT" and local_bearish:
            codes = ["HIGHER_TIMEFRAME_CONFLICT"]
        else:
            codes = ["PRICE_NOT_AT_PULLBACK_ZONE", "MOMENTUM_NOT_CONFIRMED"]
        return _no_trade(self.name, "No trend-aligned pullback", codes, required_inputs=required, missing_inputs=missing, context=context)


class MeanReversionStrategy:
    name = "Mean Reversion"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        rsi = context.momentum.get("rsi")
        required = ["rsi", "market_regime"]
        if isinstance(rsi, (int, float)) and context.market_regime in {"RANGING", "LOW_VOLATILITY"}:
            if rsi <= 30:
                return _trade(self.name, "LONG", context, 0.64, "Oversold range condition", required_inputs=required)
            if rsi >= 70:
                return _trade(self.name, "SHORT", context, 0.64, "Overbought range condition", required_inputs=required)
        codes = ["REQUIRED_DATA_MISSING"] if not isinstance(rsi, (int, float)) else ["RSI_OUTSIDE_ENTRY_RANGE", "REGIME_INCOMPATIBLE"]
        return _no_trade(self.name, "No mean reversion edge", codes, required_inputs=required, missing_inputs=[] if isinstance(rsi, (int, float)) else ["rsi"], context=context)


class LiquiditySweepStrategy:
    name = "Liquidity Sweep"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        sr = context.support_resistance
        required = ["price", "HH", "LL", "previous_session_high", "previous_session_low"]
        if price and context.market_structure.get("LL") and price > float(sr["previous_session_low"]):
            return _trade(self.name, "LONG", context, 0.62, "Sell-side sweep reclaimed previous low", required_inputs=required)
        if price and context.market_structure.get("HH") and price < float(sr["previous_session_high"]):
            return _trade(self.name, "SHORT", context, 0.62, "Buy-side sweep rejected previous high", required_inputs=required)
        return _no_trade(self.name, "No sweep and reclaim/rejection", ["STRUCTURE_NOT_CONFIRMED"], required_inputs=required, context=context)


class MarketStructureStrategy:
    name = "Market Structure"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        if context.market_structure.get("BOS") == "bullish" or context.market_structure.get("CHOCH") == "bullish":
            return _trade(self.name, "LONG", context, 0.68, "Structure shifted bullish", required_inputs=["BOS", "CHOCH"])
        if context.market_structure.get("BOS") == "bearish" or context.market_structure.get("CHOCH") == "bearish":
            return _trade(self.name, "SHORT", context, 0.68, "Structure shifted bearish", required_inputs=["BOS", "CHOCH"])
        return _no_trade(self.name, "No structural displacement", ["NO_BOS", "NO_CHOCH"], required_inputs=["BOS", "CHOCH"], context=context)


class SupportResistanceBounceStrategy:
    name = "Support Resistance Bounce"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        atr = context.atr or 0
        sr = context.support_resistance
        support = float(sr["nearest_support"])
        resistance = float(sr["nearest_resistance"])
        low = float(sr.get("current_low") or price or 0)
        high = float(sr.get("current_high") or price or 0)
        close = float(sr.get("current_close") or price or 0)
        tolerance = atr * 0.35
        if price and atr and abs(price - support) <= tolerance and low <= support + tolerance and close > support:
            return _trade(self.name, "LONG", context, 0.61, "Prior support held and current candle rejected lower prices", required_inputs=["price", "atr", "nearest_support", "current_low", "current_close"])
        if price and atr and abs(price - resistance) <= tolerance and high >= resistance - tolerance and close < resistance:
            return _trade(self.name, "SHORT", context, 0.61, "Prior resistance held and current candle rejected higher prices", required_inputs=["price", "atr", "nearest_resistance", "current_high", "current_close"])
        codes = ["SUPPORT_DISTANCE_TOO_LARGE", "RESISTANCE_DISTANCE_TOO_LARGE"]
        if price and atr and (abs(price - support) <= tolerance or abs(price - resistance) <= tolerance):
            codes = ["SUPPORT_REJECTION_NOT_CONFIRMED", "RESISTANCE_REJECTION_NOT_CONFIRMED"]
        return _no_trade(self.name, "Price is not near confirmed support/resistance rejection", codes, required_inputs=["price", "atr", "nearest_support", "nearest_resistance"], context=context)


class SupportResistanceBreakStrategy:
    name = "Support Resistance Break"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        breakout = BreakoutStrategy().evaluate(context)
        return StrategyOutput(
            strategy=self.name,
            decision=breakout.decision,
            confidence=breakout.confidence,
            risk_reward=breakout.risk_reward,
            entry=breakout.entry,
            stop=breakout.stop,
            target=breakout.target,
            reason=breakout.reason,
            valid=breakout.valid,
            strategy_name=self.name,
            direction=breakout.direction,
            eligible=breakout.eligible,
            entry_conditions_met=breakout.entry_conditions_met,
            rejection_codes=breakout.rejection_codes,
            rejection_details=breakout.rejection_details,
            required_inputs=breakout.required_inputs,
            missing_inputs=breakout.missing_inputs,
            stale_inputs=breakout.stale_inputs,
            indicator_values=breakout.indicator_values,
            thresholds_used=breakout.thresholds_used,
            calculated_risk_reward=breakout.calculated_risk_reward,
            market_regime_compatibility=breakout.market_regime_compatibility,
            higher_timeframe_compatibility=breakout.higher_timeframe_compatibility,
            conflict_classification=breakout.conflict_classification,
            geometry=breakout.geometry,
        )


class VWAPStrategy:
    name = "VWAP"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        price = _price(context)
        anchor = context.ema20
        if price and anchor and price > anchor and context.trend_strength >= 0.4:
            return _trade(self.name, "LONG", context, 0.6, "Price accepted above intraday value proxy", required_inputs=["price", "ema20", "trend_strength"])
        if price and anchor and price < anchor and context.trend_strength >= 0.4:
            return _trade(self.name, "SHORT", context, 0.6, "Price accepted below intraday value proxy", required_inputs=["price", "ema20", "trend_strength"])
        return _no_trade(self.name, "No VWAP/value acceptance", ["MOMENTUM_NOT_CONFIRMED", "REGIME_INCOMPATIBLE"], required_inputs=["price", "ema20", "trend_strength"], context=context)


class MomentumStrategy:
    name = "Momentum"

    def evaluate(self, context: MarketContext) -> StrategyOutput:
        rsi = context.momentum.get("rsi")
        macd = context.momentum.get("macd")
        signal = context.momentum.get("macd_signal")
        if isinstance(rsi, (int, float)) and isinstance(macd, (int, float)) and isinstance(signal, (int, float)):
            if rsi > 55 and macd > signal:
                return _trade(self.name, "LONG", context, 0.65, "Momentum confirms bullish continuation", required_inputs=["rsi", "macd", "macd_signal"])
            if rsi < 45 and macd < signal:
                return _trade(self.name, "SHORT", context, 0.65, "Momentum confirms bearish continuation", required_inputs=["rsi", "macd", "macd_signal"])
        missing = [name for name, value in {"rsi": rsi, "macd": macd, "macd_signal": signal}.items() if not isinstance(value, (int, float))]
        return _no_trade(self.name, "Momentum is neutral or conflicting", ["REQUIRED_DATA_MISSING"] if missing else ["MOMENTUM_NOT_CONFIRMED"], required_inputs=["rsi", "macd", "macd_signal"], missing_inputs=missing, context=context)


STRATEGIES: tuple[DeterministicStrategy, ...] = (
    EMATrendStrategy(),
    BreakoutStrategy(),
    PullbackStrategy(),
    MeanReversionStrategy(),
    LiquiditySweepStrategy(),
    MarketStructureStrategy(),
    SupportResistanceBounceStrategy(),
    SupportResistanceBreakStrategy(),
    VWAPStrategy(),
    MomentumStrategy(),
)


def evaluate_strategies(context: MarketContext) -> list[StrategyOutput]:
    return [strategy.evaluate(context) for strategy in STRATEGIES]


def _price(context: MarketContext) -> float | None:
    price = context.support_resistance.get("current_price") or context.support_resistance.get("current_close")
    return float(price) if price is not None else None


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float_optional(name: str) -> float | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _trade(strategy: str, decision: str, context: MarketContext, confidence: float, reason: str, *, required_inputs: list[str] | None = None) -> StrategyOutput:
    entry = _price(context)
    if entry is None:
        return _no_trade(strategy, "Missing entry proxy", ["REQUIRED_DATA_MISSING"], required_inputs=required_inputs or ["price"], missing_inputs=["price"], context=context)
    atr = context.atr or max(abs(float(context.support_resistance["nearest_resistance"]) - float(context.support_resistance["nearest_support"])) / 4, entry * 0.001)
    structure_level = context.support_resistance.get("nearest_support") if decision == "LONG" else context.support_resistance.get("nearest_resistance")
    spread_price = float(context.spread) * _pip_size(context.symbol) if context.spread is not None else None
    stop = construct_dynamic_stop(
        decision,
        entry,
        float(structure_level) if structure_level is not None else None,
        atr,
        spread_price,
        min_atr_mult=_env_float("STRATEGY_SL_MIN_ATR_MULT", 1.0),
        max_atr_mult=_env_float("STRATEGY_SL_MAX_ATR_MULT", 3.0),
        min_structure_buffer=_env_float("STRATEGY_SL_MIN_STRUCTURE_BUFFER", 0.0),
        max_stop_distance=_env_float_optional("STRATEGY_SL_MAX_DISTANCE"),
        min_spread_ratio=_env_float("STRATEGY_SL_MIN_SPREAD_RATIO", 3.0),
    )
    if stop is None:
        return _no_trade(
            strategy,
            "No valid stop could be constructed within configured ATR/structure/spread bounds",
            ["NO_VALID_STOP_CONSTRUCTED"],
            required_inputs=required_inputs,
            context=context,
        )
    stop_distance = abs(entry - stop)
    target = entry + stop_distance * 1.8 if decision == "LONG" else entry - stop_distance * 1.8
    geometry = entry_geometry(decision, entry, stop, target, context)
    codes = [] if geometry["valid"] else list(geometry["rejection_codes"])
    return StrategyOutput(
        strategy=strategy,
        decision=decision if geometry["valid"] else "NO_TRADE",
        confidence=confidence if geometry["valid"] else 0.0,
        risk_reward=float(geometry["risk_reward"] or 0),
        entry=entry,
        stop=stop,
        target=target,
        reason=reason if geometry["valid"] else "Entry geometry rejected",
        valid=geometry["valid"],
        strategy_name=strategy,
        direction=decision,
        eligible=geometry["valid"],
        entry_conditions_met=geometry["valid"],
        rejection_codes=codes,
        rejection_details={"geometry": geometry} if codes else {},
        required_inputs=required_inputs or [],
        missing_inputs=[],
        stale_inputs=[],
        indicator_values=_indicator_values(context),
        thresholds_used={"risk_reward_min": 1.0},
        calculated_risk_reward=float(geometry["risk_reward"] or 0),
        market_regime_compatibility="COMPATIBLE",
        higher_timeframe_compatibility=classify_higher_timeframe(decision, context.higher_timeframe_trend),
        geometry=geometry,
    )


def _no_trade(
    strategy: str,
    reason: str,
    rejection_codes: list[str],
    *,
    required_inputs: list[str] | None = None,
    missing_inputs: list[str] | None = None,
    context: MarketContext | None = None,
) -> StrategyOutput:
    return StrategyOutput(
        strategy=strategy,
        decision="NO_TRADE",
        confidence=0.0,
        risk_reward=0.0,
        entry=None,
        stop=None,
        target=None,
        reason=reason,
        valid=False,
        strategy_name=strategy,
        direction="NO_TRADE",
        eligible=False,
        entry_conditions_met=False,
        rejection_codes=rejection_codes,
        rejection_details={"reason": reason},
        required_inputs=required_inputs or [],
        missing_inputs=missing_inputs or [],
        stale_inputs=[],
        indicator_values=_indicator_values(context) if context else {},
        thresholds_used={},
        calculated_risk_reward=0.0,
        market_regime_compatibility="INCOMPATIBLE" if "REGIME_INCOMPATIBLE" in rejection_codes else "UNKNOWN",
        higher_timeframe_compatibility="CONFLICT" if "HIGHER_TIMEFRAME_CONFLICT" in rejection_codes else ("UNAVAILABLE" if context and str(context.higher_timeframe_trend).lower() in {"unknown", "unavailable", ""} else "UNKNOWN"),
        geometry={},
    )


def entry_geometry(direction: str, entry: float, stop: float, target: float, context: MarketContext | None = None) -> dict[str, Any]:
    stop_distance = abs(entry - stop)
    target_distance = abs(target - entry)
    risk_reward = target_distance / stop_distance if stop_distance > 0 else 0.0
    valid_long = direction == "LONG" and stop < entry < target
    valid_short = direction == "SHORT" and target < entry < stop
    codes: list[str] = []
    if stop_distance <= 0:
        codes.append("STOP_DISTANCE_INVALID")
    if target_distance <= 0:
        codes.append("TARGET_DISTANCE_INVALID")
    if direction == "LONG" and not valid_long:
        codes.append("LONG_GEOMETRY_INVALID")
    if direction == "SHORT" and not valid_short:
        codes.append("SHORT_GEOMETRY_INVALID")
    if risk_reward < 1.0:
        codes.append("RISK_REWARD_TOO_LOW")
    atr = float(context.atr or 0) if context else 0.0
    sr = context.support_resistance if context else {}
    nearest_support = float(sr.get("nearest_support") or entry)
    nearest_resistance = float(sr.get("nearest_resistance") or entry)
    return {
        "valid": not codes,
        "rejection_codes": codes,
        "entry": entry,
        "stop": stop,
        "target": target,
        "stop_distance_price": stop_distance,
        "stop_distance_pips": stop_distance * 10000,
        "target_distance_price": target_distance,
        "target_distance_pips": target_distance * 10000,
        "risk_reward": risk_reward,
        "spread": float(getattr(context, "spread", 0) or 0) if context else 0,
        "atr": atr,
        "stop_atr_multiple": stop_distance / atr if atr else None,
        "target_atr_multiple": target_distance / atr if atr else None,
        "nearest_support_distance": abs(entry - nearest_support),
        "nearest_resistance_distance": abs(nearest_resistance - entry),
    }


def _missing(values: dict[str, Any]) -> list[str]:
    return [key for key, value in values.items() if value is None]


def _indicator_values(context: MarketContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        "ema20": context.ema20,
        "ema50": context.ema50,
        "ema200": context.ema200,
        "ema20_slope": context.ema20_slope,
        "rsi": context.momentum.get("rsi"),
        "macd": context.momentum.get("macd"),
        "macd_signal": context.momentum.get("macd_signal"),
        "atr": context.atr,
        "trend_strength": context.trend_strength,
        "market_regime": context.market_regime,
        "higher_timeframe_trend": context.higher_timeframe_trend,
    }


def classify_higher_timeframe(direction: str, trend: str | None) -> str:
    normalized = str(trend or "unknown").lower()
    if normalized in {"unknown", "unavailable", "missing", ""}:
        return "UNAVAILABLE"
    if normalized in {"neutral", "ranging", "mixed"}:
        return "NEUTRAL"
    if direction == "LONG" and normalized == "bullish":
        return "ALIGNED"
    if direction == "SHORT" and normalized == "bearish":
        return "ALIGNED"
    if normalized in {"bullish", "bearish"}:
        return "CONFLICT"
    return "UNAVAILABLE"

from __future__ import annotations

from abc import ABC, abstractmethod
from statistics import mean, pstdev
from typing import Any

from backend.forex_frameworks.models import FrameworkBias, FrameworkContext, FrameworkSignal, FrameworkStatus, PriceZone, SignalStatus


def _bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _close(ctx: FrameworkContext) -> float:
    return float(ctx.current_feature.get("close") or (ctx.completed_candles[-1].get("c") if ctx.completed_candles else 0) or 0)


def _bias_from_score(score: float) -> FrameworkBias:
    if score >= 0.75:
        return FrameworkBias.STRONGLY_BULLISH
    if score >= 0.45:
        return FrameworkBias.BULLISH
    if score >= 0.15:
        return FrameworkBias.SLIGHTLY_BULLISH
    if score <= -0.75:
        return FrameworkBias.STRONGLY_BEARISH
    if score <= -0.45:
        return FrameworkBias.BEARISH
    if score <= -0.15:
        return FrameworkBias.SLIGHTLY_BEARISH
    return FrameworkBias.NEUTRAL


def _signal(
    framework: "ForexFramework",
    ctx: FrameworkContext,
    *,
    bias: FrameworkBias,
    signal_type: str,
    confidence: float,
    quality: float,
    status: SignalStatus = SignalStatus.VALID,
    supporting: list[str] | None = None,
    conflicting: list[str] | None = None,
    missing: list[str] | None = None,
    limitations: list[str] | None = None,
    entry: PriceZone | None = None,
    invalidation: PriceZone | None = None,
    targets: list[PriceZone] | None = None,
    metadata: dict[str, Any] | None = None,
) -> FrameworkSignal:
    return FrameworkSignal(
        framework_id=framework.framework_id,
        framework_name=framework.display_name,
        framework_version=framework.version,
        symbol=ctx.symbol,
        timeframe=ctx.timeframe,
        analysis_timestamp=ctx.analysis_timestamp,
        bias=bias,
        signal_type=signal_type,
        confidence=_bounded(confidence),
        quality=_bounded(quality),
        entry_zone=entry,
        invalidation_zone=invalidation,
        target_zones=targets or [],
        risk_reward_estimate=metadata.get("risk_reward_estimate") if metadata else None,
        market_regime=str(ctx.regime.get("label") or "unknown"),
        supporting_evidence=supporting or [],
        conflicting_evidence=conflicting or [],
        missing_evidence=missing or [],
        limitations=limitations or [],
        status=status,
        metadata=metadata or {},
    )


class ForexFramework(ABC):
    framework_id: str
    display_name: str
    version = "1.0.0"
    status = FrameworkStatus.IMPLEMENTED
    group = "Quantitative"
    supported_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    limitations: list[str] = []
    dependencies = ["forex_intelligence"]
    weight = 1.0

    def __init__(self, supported_symbols: set[str]) -> None:
        self.supported_symbols = supported_symbols

    def supports(self, ctx: FrameworkContext) -> bool:
        return ctx.symbol in self.supported_symbols and ctx.timeframe in self.supported_timeframes

    @abstractmethod
    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        ...

    def unsupported(self, ctx: FrameworkContext) -> FrameworkSignal:
        return _signal(
            self,
            ctx,
            bias=FrameworkBias.UNKNOWN,
            signal_type="unsupported",
            confidence=0,
            quality=0,
            status=SignalStatus.UNSUPPORTED_INSTRUMENT if ctx.symbol not in self.supported_symbols else SignalStatus.INVALID,
            missing=["supported symbol/timeframe"],
            limitations=self.limitations,
        )


class TrendFollowingFramework(ForexFramework):
    framework_id = "trend_following"
    display_name = "Trend Following"
    weight = 1.15

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        if not self.supports(ctx):
            return self.unsupported(ctx)
        trend_score = float(ctx.indicators.get("trend_score") or ctx.current_feature.get("trend_score") or 0)
        adx = float(ctx.indicators.get("adx") or 0)
        slope = float(ctx.indicators.get("regression_slope") or 0)
        ema_fast = ctx.indicators.get("ema_fast")
        ema_slow = ctx.indicators.get("ema_slow")
        score = trend_score + (0.15 if adx >= 25 and trend_score > 0 else -0.15 if adx >= 25 and trend_score < 0 else 0)
        supporting = [f"trend_score={trend_score:.3f}", f"adx={adx:.2f}", f"regression_slope={slope:.6f}"]
        if ema_fast is not None and ema_slow is not None:
            supporting.append("ema_fast_above_slow" if float(ema_fast) > float(ema_slow) else "ema_fast_below_slow")
        conflicting = []
        if ctx.regime.get("label") == "range":
            conflicting.append("range regime reduces trend continuation confidence")
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type="trend_continuation", confidence=min(0.9, abs(score)), quality=0.8, supporting=supporting, conflicting=conflicting)


class MeanReversionFramework(ForexFramework):
    framework_id = "mean_reversion"
    display_name = "Mean Reversion"
    weight = 0.85

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        closes = [float(row.get("c", row.get("close", 0)) or 0) for row in ctx.completed_candles[-40:]]
        if len(closes) < 20:
            return _signal(self, ctx, bias=FrameworkBias.UNKNOWN, signal_type="insufficient_range", confidence=0, quality=0.2, status=SignalStatus.INSUFFICIENT_DATA, missing=["20 completed candles"])
        avg = mean(closes)
        dev = pstdev(closes) or 1e-9
        z = (closes[-1] - avg) / dev
        rsi = float(ctx.indicators.get("rsi") or 50)
        score = -_bounded(z / 2.5, -1, 1)
        if rsi >= 70:
            score -= 0.25
        if rsi <= 30:
            score += 0.25
        confidence = min(0.75, abs(score)) if ctx.regime.get("label") not in {"trend", "breakout"} else min(0.35, abs(score))
        conflicting = ["trend/breakout regime reduces mean-reversion confidence"] if ctx.regime.get("label") in {"trend", "breakout"} else []
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type="equilibrium_reversion", confidence=confidence, quality=0.7, supporting=[f"z_score={z:.2f}", f"rsi={rsi:.2f}"], conflicting=conflicting)


class MomentumFramework(ForexFramework):
    framework_id = "momentum"
    display_name = "Momentum"
    weight = 1.05

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        momentum = float(ctx.indicators.get("momentum_score") or ctx.current_feature.get("momentum_score") or 0)
        macd = float(ctx.indicators.get("macd") or 0)
        signal = float(ctx.indicators.get("macd_signal") or 0)
        roc = float(ctx.indicators.get("roc") or 0)
        score = momentum + (0.15 if macd > signal else -0.15 if macd < signal else 0)
        signal_type = "momentum_continuation" if abs(score) >= 0.25 else "momentum_exhaustion"
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type=signal_type, confidence=min(0.85, abs(score)), quality=0.75, supporting=[f"momentum_score={momentum:.3f}", f"macd_delta={macd - signal:.6f}", f"roc={roc:.6f}"])


class BreakoutFramework(ForexFramework):
    framework_id = "breakout"
    display_name = "Breakout"
    weight = 0.95

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        feature = ctx.current_feature
        latest_break = feature.get("latest_break")
        direction = str(feature.get("latest_break_direction") or "")
        volatility = str(feature.get("volatility_state") or "")
        score = 0.0
        if latest_break and direction == "bullish":
            score += 0.55
        if latest_break and direction == "bearish":
            score -= 0.55
        if volatility == "expansion":
            score += 0.15 if score >= 0 else -0.15 if score < 0 else 0
        status = SignalStatus.VALID if latest_break else SignalStatus.PARTIAL
        missing = [] if latest_break else ["completed breakout confirmation"]
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type="completed_breakout" if latest_break else "breakout_watch", confidence=min(0.8, abs(score)), quality=0.7 if latest_break else 0.45, status=status, supporting=[f"latest_break={latest_break}", f"volatility={volatility}"], missing=missing)


class PriceActionFramework(ForexFramework):
    framework_id = "price_action"
    display_name = "Price Action"
    group = "Price Action"

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        candles = ctx.completed_candles[-3:]
        if len(candles) < 3:
            return _signal(self, ctx, bias=FrameworkBias.UNKNOWN, signal_type="insufficient_candles", confidence=0, quality=0.2, status=SignalStatus.INSUFFICIENT_DATA, missing=["3 completed candles"])
        prev, cur = candles[-2], candles[-1]
        o, c, h, l = [float(cur.get(k, 0) or 0) for k in ("o", "c", "h", "l")]
        po, pc = float(prev.get("o", 0) or 0), float(prev.get("c", 0) or 0)
        body = abs(c - o)
        wick = max(h - max(o, c), min(o, c) - l)
        score = 0.0
        patterns = []
        if c > o and pc < po and c >= po and o <= pc:
            score += 0.55
            patterns.append("bullish_engulfing")
        elif c < o and pc > po and c <= po and o >= pc:
            score -= 0.55
            patterns.append("bearish_engulfing")
        if wick > body * 2:
            patterns.append("rejection_candle")
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type=",".join(patterns) or "no_clear_pattern", confidence=min(0.65, abs(score) + (0.1 if patterns else 0)), quality=0.65, supporting=patterns or ["no deterministic candle pattern"])


class SupportResistanceFramework(ForexFramework):
    framework_id = "support_resistance"
    display_name = "Support and Resistance"
    group = "Price Action"

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        levels = ctx.market_structure.get("liquidity_levels", []) or []
        close = _close(ctx)
        nearby = [level for level in levels if abs(float(level.get("price", close)) - close) <= close * 0.003]
        bias = FrameworkBias.NEUTRAL
        if nearby:
            side = nearby[0].get("side") or nearby[0].get("kind") or "level"
            bias = FrameworkBias.SLIGHTLY_BULLISH if "sell" in str(side).lower() else FrameworkBias.SLIGHTLY_BEARISH if "buy" in str(side).lower() else FrameworkBias.NEUTRAL
        return _signal(self, ctx, bias=bias, signal_type="zone_reaction", confidence=0.45 if nearby else 0.2, quality=0.55, status=SignalStatus.PARTIAL if not nearby else SignalStatus.VALID, supporting=[f"nearby_zones={len(nearby)}"], missing=[] if nearby else ["nearby tested support/resistance zone"])


class SupplyDemandFramework(ForexFramework):
    framework_id = "supply_demand"
    display_name = "Supply and Demand"
    group = "Price Action"

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        blocks = ctx.market_structure.get("order_blocks", []) or []
        active = [block for block in blocks if not block.get("invalidation_time")]
        feature = ctx.current_feature
        pd = feature.get("premium_discount")
        bias = FrameworkBias.SLIGHTLY_BULLISH if pd == "discount" else FrameworkBias.SLIGHTLY_BEARISH if pd == "premium" else FrameworkBias.NEUTRAL
        return _signal(self, ctx, bias=bias, signal_type="fresh_zone_context" if active else "zone_watch", confidence=0.5 if active else 0.25, quality=0.55, status=SignalStatus.VALID if active else SignalStatus.PARTIAL, supporting=[f"active_order_blocks={len(active)}", f"premium_discount={pd}"], missing=[] if active else ["fresh supply/demand zone"])


class SMCFramework(ForexFramework):
    framework_id = "smc"
    display_name = "Smart Money Concepts"
    group = "Smart Money"
    weight = 0.85

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        feature = ctx.current_feature
        score = float(feature.get("trend_score") or 0) * 0.4 + float(feature.get("momentum_score") or 0) * 0.2
        if feature.get("liquidity_sweeps", 0):
            score += 0.15 if score >= 0 else -0.15
        return _signal(self, ctx, bias=_bias_from_score(score), signal_type="structure_liquidity_context", confidence=min(0.75, abs(score) + 0.2), quality=0.7, supporting=[f"latest_break={feature.get('latest_break')}", f"liquidity_sweeps={feature.get('liquidity_sweeps')}", f"active_fvgs={feature.get('active_fvgs')}", f"premium_discount={feature.get('premium_discount')}"], limitations=["framework interpretation reuses objective market-structure engine"])


class ICTFramework(SMCFramework):
    framework_id = "ict"
    display_name = "ICT Interpretation"
    weight = 0.75

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        signal = super().analyze(ctx)
        signal.framework_id = self.framework_id
        signal.framework_name = self.display_name
        signal.signal_type = "liquidity_draw_interpretation"
        signal.supporting_evidence.append(f"session={ctx.current_feature.get('session')}")
        signal.limitations.append("ICT terms are framework-specific interpretation, not guaranteed institutional-order insight")
        return signal


class LimitedDataFramework(ForexFramework):
    status = FrameworkStatus.LIMITED
    group = "Experimental"
    weight = 0.35

    def __init__(self, supported_symbols: set[str], framework_id: str, display_name: str, group: str, status: FrameworkStatus = FrameworkStatus.LIMITED) -> None:
        super().__init__(supported_symbols)
        self.framework_id = framework_id
        self.display_name = display_name
        self.group = group
        self.status = status

    def analyze(self, ctx: FrameworkContext) -> FrameworkSignal:
        missing = ["validated framework-specific data feed"]
        limitations = ["registered for evidence normalization; current output is limited/research and not a validated signal"]
        if self.framework_id in {"vsa", "volume_profile", "market_profile", "wyckoff"}:
            missing.append("centralized forex volume" if ctx.asset_type == "forex" else "exchange-quality volume")
            limitations.append("tick volume or proxy volume is not centralized exchange volume")
        if self.framework_id in {"carry_macro", "correlation_intermarket"}:
            missing.append("macro/intermarket synchronized data")
        return _signal(self, ctx, bias=FrameworkBias.UNKNOWN, signal_type="limited_data", confidence=0, quality=0.25, status=SignalStatus.INSUFFICIENT_DATA, missing=missing, limitations=limitations)

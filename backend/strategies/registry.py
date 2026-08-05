from __future__ import annotations

from backend.market_data.models import AssetClass
from backend.strategies.models import (
    Capability,
    Condition,
    CooldownRule,
    DataPolicy,
    EntryRules,
    InvalidationRule,
    RuleGroup,
    StrategyFamily,
    StrategyMetadata,
    StrategyRegistration,
    StrategySpec,
    StrategyStatus,
    TargetRule,
)


def get_strategy_registry() -> dict[str, StrategySpec]:
    specs = [
        _ema_trend_continuation(),
        _rsi_mean_reversion(),
        _donchian_breakout(),
        _smc_liquidity_reversal(),
        _smc_continuation(),
    ]
    return {spec.strategy.id: spec for spec in specs}


def list_strategy_registrations() -> list[StrategyRegistration]:
    registrations: list[StrategyRegistration] = []
    for spec in get_strategy_registry().values():
        registrations.append(
            StrategyRegistration(
                strategy_id=spec.strategy.id,
                name=spec.strategy.name,
                family=spec.strategy.family,
                version=spec.strategy.version,
                status=spec.strategy.status,
                supported_asset_classes=spec.universe.asset_classes,
                supported_timeframes=[spec.timeframes.execution, *spec.timeframes.context],
                required_capabilities=_capabilities_for(spec),
                required_features=spec.required_features,
                required_history=spec.required_history,
                allows_long=spec.entry.long is not None,
                allows_short=spec.entry.short is not None,
                supports_multi_timeframe=bool(spec.timeframes.context),
                experimental=spec.strategy.status != StrategyStatus.VALIDATED,
            )
        )
    return registrations


def get_strategy(strategy_id: str) -> StrategySpec | None:
    return get_strategy_registry().get(strategy_id)


def _capabilities_for(spec: StrategySpec) -> list[Capability]:
    caps = {Capability.OHLCV, Capability.INDICATORS, Capability.DATA_QUALITY}
    if any(feature.startswith(("structure.", "liquidity.", "fvg.", "order_block.", "dealing_range.")) for feature in spec.required_features):
        caps.add(Capability.MARKET_STRUCTURE)
    return sorted(caps, key=lambda item: item.value)


def _ema_trend_continuation() -> StrategySpec:
    return StrategySpec(
        strategy=StrategyMetadata(
            id="ema_trend_continuation_v1",
            name="EMA Trend Continuation",
            version="1.0.0",
            family=StrategyFamily.TREND_FOLLOWING,
            status=StrategyStatus.RESEARCH,
            description="Deterministic continuation setup using EMA alignment and candle direction.",
            immutable=True,
        ),
        data_policy=DataPolicy(minimum_quality_score=0.5),
        required_history=60,
        required_features=["indicator.ema.20", "indicator.ema.50", "price.close", "price.is_bullish", "price.is_bearish"],
        entry=EntryRules(
            long=RuleGroup(all=[
                Condition(feature="indicator.ema.20", operator="gt", value=0),
                Condition(feature="indicator.ema.50", operator="gt", value=0),
                Condition(feature="indicator.ema.20", operator="gt", value="indicator.ema.50"),
                Condition(feature="price.is_bullish", operator="is_true"),
            ]),
            short=RuleGroup(all=[
                Condition(feature="indicator.ema.20", operator="gt", value=0),
                Condition(feature="indicator.ema.50", operator="gt", value=0),
                Condition(feature="indicator.ema.20", operator="lt", value="indicator.ema.50"),
                Condition(feature="price.is_bearish", operator="is_true"),
            ]),
        ),
        invalidation=InvalidationRule(type="atr_distance", value=1.5),
        targets=[TargetRule(type="risk_reward", value=2.0)],
        cooldown=CooldownRule(bars=3),
    )


def _rsi_mean_reversion() -> StrategySpec:
    return StrategySpec(
        strategy=StrategyMetadata(
            id="rsi_mean_reversion_v1",
            name="RSI Mean Reversion",
            version="1.0.0",
            family=StrategyFamily.MEAN_REVERSION,
            status=StrategyStatus.RESEARCH,
            description="Oversold/overbought RSI setup with confirming candle direction.",
            immutable=True,
        ),
        required_history=30,
        required_features=["indicator.rsi.14", "price.is_bullish", "price.is_bearish"],
        entry=EntryRules(
            long=RuleGroup(all=[Condition(feature="indicator.rsi.14", operator="lte", value=30), Condition(feature="price.is_bullish", operator="is_true")]),
            short=RuleGroup(all=[Condition(feature="indicator.rsi.14", operator="gte", value=70), Condition(feature="price.is_bearish", operator="is_true")]),
        ),
        invalidation=InvalidationRule(type="atr_distance", value=1.25),
        targets=[TargetRule(type="risk_reward", value=1.5)],
        cooldown=CooldownRule(bars=5),
    )


def _donchian_breakout() -> StrategySpec:
    return StrategySpec(
        strategy=StrategyMetadata(
            id="donchian_breakout_v1",
            name="Donchian Breakout",
            version="1.0.0",
            family=StrategyFamily.BREAKOUT,
            status=StrategyStatus.RESEARCH,
            description="Breakout through the prior 20-bar channel.",
            immutable=True,
        ),
        required_history=30,
        required_features=["indicator.donchian.high.20", "indicator.donchian.low.20", "price.close"],
        entry=EntryRules(
            long=RuleGroup(all=[Condition(feature="price.close", operator="gt", value="indicator.donchian.high.20")]),
            short=RuleGroup(all=[Condition(feature="price.close", operator="lt", value="indicator.donchian.low.20")]),
        ),
        invalidation=InvalidationRule(type="atr_distance", value=2.0),
        targets=[TargetRule(type="risk_reward", value=2.5)],
    )


def _smc_liquidity_reversal() -> StrategySpec:
    return StrategySpec(
        strategy=StrategyMetadata(
            id="smc_liquidity_reversal_v1",
            name="SMC Liquidity Reversal",
            version="1.0.0",
            family=StrategyFamily.SMC,
            status=StrategyStatus.RESEARCH,
            description="Liquidity sweep followed by displacement/market-structure reversal evidence.",
            immutable=True,
        ),
        required_history=120,
        required_features=[
            "liquidity.sweep.direction",
            "structure.latest_break.type",
            "structure.latest_break.direction",
            "dealing_range.position",
            "fvg.inside_bullish",
            "fvg.inside_bearish",
            "order_block.inside_bullish",
            "order_block.inside_bearish",
        ],
        entry=EntryRules(
            long=RuleGroup(all=[
                Condition(feature="liquidity.sweep.direction", operator="eq", value="sell_side"),
                Condition(feature="structure.latest_break.type", operator="in", value=["choch", "mss"]),
                Condition(feature="structure.latest_break.direction", operator="eq", value="bullish"),
                Condition(feature="dealing_range.position", operator="lte", value=0.6),
                RuleGroup(any=[Condition(feature="fvg.inside_bullish", operator="is_true"), Condition(feature="order_block.inside_bullish", operator="is_true")]),
            ]),
            short=RuleGroup(all=[
                Condition(feature="liquidity.sweep.direction", operator="eq", value="buy_side"),
                Condition(feature="structure.latest_break.type", operator="in", value=["choch", "mss"]),
                Condition(feature="structure.latest_break.direction", operator="eq", value="bearish"),
                Condition(feature="dealing_range.position", operator="gte", value=0.4),
                RuleGroup(any=[Condition(feature="fvg.inside_bearish", operator="is_true"), Condition(feature="order_block.inside_bearish", operator="is_true")]),
            ]),
        ),
        invalidation=InvalidationRule(type="atr_distance", value=1.0),
        targets=[TargetRule(type="risk_reward", value=2.0)],
    )


def _smc_continuation() -> StrategySpec:
    return StrategySpec(
        strategy=StrategyMetadata(
            id="smc_continuation_v1",
            name="SMC Continuation",
            version="1.0.0",
            family=StrategyFamily.SMC,
            status=StrategyStatus.RESEARCH,
            description="Continuation setup after BOS with retracement into deterministic imbalance/order-block evidence.",
            immutable=True,
        ),
        required_history=120,
        required_features=["structure.trend", "structure.latest_break.type", "structure.latest_break.direction", "fvg.inside_bullish", "fvg.inside_bearish", "order_block.inside_bullish", "order_block.inside_bearish"],
        entry=EntryRules(
            long=RuleGroup(all=[
                Condition(feature="structure.trend", operator="eq", value="bullish"),
                Condition(feature="structure.latest_break.type", operator="eq", value="bos"),
                Condition(feature="structure.latest_break.direction", operator="eq", value="bullish"),
                RuleGroup(any=[Condition(feature="fvg.inside_bullish", operator="is_true"), Condition(feature="order_block.inside_bullish", operator="is_true")]),
            ]),
            short=RuleGroup(all=[
                Condition(feature="structure.trend", operator="eq", value="bearish"),
                Condition(feature="structure.latest_break.type", operator="eq", value="bos"),
                Condition(feature="structure.latest_break.direction", operator="eq", value="bearish"),
                RuleGroup(any=[Condition(feature="fvg.inside_bearish", operator="is_true"), Condition(feature="order_block.inside_bearish", operator="is_true")]),
            ]),
        ),
        invalidation=InvalidationRule(type="atr_distance", value=1.2),
        targets=[TargetRule(type="risk_reward", value=2.0)],
    )

from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, LiquidityLevel, LiquiditySide, LiquiditySweep, SwingPoint, stable_id


def _tolerance(level: Decimal, atr: Decimal | None, config: MarketStructureConfig) -> Decimal:
    vals = [Decimal("0")]
    if config.equal_levels.tolerance_absolute is not None:
        vals.append(Decimal(str(config.equal_levels.tolerance_absolute)))
    if config.equal_levels.tolerance_percent is not None:
        vals.append(abs(level) * Decimal(str(config.equal_levels.tolerance_percent)))
    if atr is not None:
        vals.append(atr * Decimal(str(config.equal_levels.tolerance_atr)))
    return max(vals)


def detect_liquidity_levels(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[LiquidityLevel]:
    atrs = average_true_range(bars, config.displacement.atr_period)
    levels: list[LiquidityLevel] = []
    for swing in swings:
        atr = atrs[swing.bar_index] if swing.bar_index < len(atrs) else None
        side = LiquiditySide.BUY_SIDE if swing.swing_type == "high" else LiquiditySide.SELL_SIDE
        levels.append(
            LiquidityLevel(
                id=stable_id("liq", symbol, timeframe, swing.id, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=swing.start_time,
                end_time=swing.end_time,
                detected_time=swing.confirmation_time or swing.detected_time,
                confirmation_time=swing.confirmation_time,
                price_low=swing.price,
                price_high=swing.price,
                direction=Direction.BULLISH if side == LiquiditySide.BUY_SIDE else Direction.BEARISH,
                status=ConceptStatus.ACTIVE,
                quality_score=0.65,
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=[swing.bar_index],
                supporting_event_ids=[swing.id],
                side=side,
                level=swing.price,
                source="confirmed_swing",
                tolerance=_tolerance(swing.price, atr, config),
            )
        )
    return levels


def detect_liquidity_sweeps(
    bars: list[StructureBar],
    levels: list[LiquidityLevel],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[LiquiditySweep]:
    sweeps: list[LiquiditySweep] = []
    active: list[LiquidityLevel] = []
    ordered = sorted(levels, key=lambda item: item.confirmation_time or item.detected_time)
    cursor = 0
    for idx, bar in enumerate(bars):
        while cursor < len(ordered):
            level = ordered[cursor]
            if not level.confirmation_time or level.confirmation_time >= bar.close_time:
                break
            active.append(level)
            cursor += 1
        remaining: list[LiquidityLevel] = []
        for level in active:
            if level.side == LiquiditySide.BUY_SIDE:
                breached = bar.high > level.level + level.tolerance
                reclaimed = bar.close < level.level
                accepted = bar.close > level.level + level.tolerance
                penetration = bar.high - level.level
                swept_price = bar.high
                direction = Direction.BEARISH
            else:
                breached = bar.low < level.level - level.tolerance
                reclaimed = bar.close > level.level
                accepted = bar.close < level.level - level.tolerance
                penetration = level.level - bar.low
                swept_price = bar.low
                direction = Direction.BULLISH
            if breached and reclaimed:
                item = LiquiditySweep(
                    id=stable_id("swp", symbol, timeframe, level.id, idx, config.configuration_hash()),
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=bar.open_time,
                    end_time=bar.close_time,
                    detected_time=bar.close_time,
                    confirmation_time=bar.close_time,
                    price_low=min(level.level, swept_price),
                    price_high=max(level.level, swept_price),
                    direction=direction,
                    status=ConceptStatus.SWEPT,
                    strength=float(penetration / max(level.tolerance, Decimal("0.00000001"))),
                    quality_score=0.75,
                    configuration_version=config.version,
                    configuration_hash=config.configuration_hash(),
                    source_dataset_id=source_dataset_id,
                    supporting_bar_indexes=level.supporting_bar_indexes + [idx],
                    supporting_event_ids=[level.id],
                    level_id=level.id,
                    side=level.side,
                    swept_price=swept_price,
                    reclaim_price=bar.close,
                    penetration=penetration,
                    bar_index=idx,
                )
                sweeps.append(item)
                continue
            if accepted:
                continue
            remaining.append(level)
        active = remaining[-500:]
    return sweeps

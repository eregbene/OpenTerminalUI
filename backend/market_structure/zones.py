from __future__ import annotations

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, OrderBlock, StructureBreak, stable_id


def detect_order_blocks(
    bars: list[StructureBar],
    breaks: list[StructureBreak],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[OrderBlock]:
    if not config.order_blocks.enabled:
        return []
    blocks: list[OrderBlock] = []
    for brk in breaks:
        if config.order_blocks.require_structure_break and brk.break_kind == "unclassified_break":
            continue
        start = max(0, brk.bar_index - config.order_blocks.max_search_bars)
        origin_idx = None
        for idx in range(brk.bar_index - 1, start - 1, -1):
            bar = bars[idx]
            bearish_candle = bar.close < bar.open
            bullish_candle = bar.close > bar.open
            if brk.direction == Direction.BULLISH and bearish_candle:
                origin_idx = idx
                break
            if brk.direction == Direction.BEARISH and bullish_candle:
                origin_idx = idx
                break
        if origin_idx is None:
            continue
        origin = bars[origin_idx]
        status = ConceptStatus.ACTIVE
        mitigation_time = None
        invalidation_time = None
        for later in bars[brk.bar_index + 1 :]:
            touched = later.low <= origin.high and later.high >= origin.low
            invalidated = later.close < origin.low if brk.direction == Direction.BULLISH else later.close > origin.high
            if touched and mitigation_time is None:
                mitigation_time = later.close_time
                status = ConceptStatus.PARTIAL
            if invalidated:
                invalidation_time = later.close_time
                status = ConceptStatus.INVALIDATED
                break
        blocks.append(
            OrderBlock(
                id=stable_id("ob", symbol, timeframe, brk.id, origin_idx, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=origin.open_time,
                end_time=brk.end_time,
                detected_time=brk.confirmation_time or brk.detected_time,
                confirmation_time=brk.confirmation_time,
                price_low=origin.low if config.order_blocks.zone_source == "full_range" else min(origin.open, origin.close),
                price_high=origin.high if config.order_blocks.zone_source == "full_range" else max(origin.open, origin.close),
                direction=brk.direction,
                status=status,
                strength=brk.strength,
                quality_score=brk.quality_score,
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=[origin_idx, brk.bar_index],
                supporting_event_ids=[brk.id],
                invalidation_time=invalidation_time,
                mitigation_time=mitigation_time,
                source_break_id=brk.id,
                origin_bar_index=origin_idx,
                rule=config.order_blocks.method,
            )
        )
    return blocks

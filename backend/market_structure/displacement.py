from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, DisplacementEvent, stable_id


def detect_displacements(
    bars: list[StructureBar],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[DisplacementEvent]:
    atrs = average_true_range(bars, config.displacement.atr_period)
    events: list[DisplacementEvent] = []
    for idx, bar in enumerate(bars):
        atr = atrs[idx]
        if not atr or atr <= 0:
            continue
        body = abs(bar.close - bar.open)
        candle_range = max(bar.high - bar.low, Decimal("0.00000001"))
        body_ratio = float(body / candle_range)
        magnitude_atr = float(candle_range / atr)
        body_atr = float(body / atr)
        direction = Direction.BULLISH if bar.close > bar.open else Direction.BEARISH if bar.close < bar.open else Direction.NEUTRAL
        if body_atr < config.displacement.body_atr and magnitude_atr < config.displacement.range_atr:
            continue
        if body_ratio < config.displacement.minimum_body_ratio:
            continue
        events.append(
            DisplacementEvent(
                id=stable_id("disp", symbol, timeframe, idx, direction, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=bar.open_time,
                end_time=bar.close_time,
                detected_time=bar.close_time,
                confirmation_time=bar.close_time,
                price_low=bar.low,
                price_high=bar.high,
                direction=direction,
                status=ConceptStatus.CONFIRMED,
                strength=max(body_atr, magnitude_atr),
                quality_score=min(1.0, max(body_atr, magnitude_atr) / 3.0),
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=[idx],
                bar_index=idx,
                magnitude_atr=magnitude_atr,
                body_ratio=body_ratio,
                bars_in_sequence=1,
                created_imbalance=False,
            )
        )
    return events

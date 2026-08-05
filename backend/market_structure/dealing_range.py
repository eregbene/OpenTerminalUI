from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, DealingRange, Direction, PremiumDiscountZone, SwingPoint, stable_id


def build_dealing_ranges(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> tuple[list[DealingRange], list[PremiumDiscountZone]]:
    if not config.dealing_range.enabled:
        return [], []
    highs = [s for s in swings if s.swing_type == "high"]
    lows = [s for s in swings if s.swing_type == "low"]
    if not highs or not lows or not bars:
        return [], []
    high = highs[-1]
    low = lows[-1]
    range_high = high.price
    range_low = low.price
    if range_high <= range_low:
        range_high, range_low = range_low, range_high
    span = max(range_high - range_low, Decimal("0.00000001"))
    current_position = float((bars[-1].close - range_low) / span)
    current_position = max(0.0, min(1.0, current_position))
    direction = Direction.BULLISH if low.bar_index < high.bar_index else Direction.BEARISH
    rng = DealingRange(
        id=stable_id("rng", symbol, timeframe, high.id, low.id, config.configuration_hash()),
        symbol=symbol,
        timeframe=timeframe,
        start_time=min(high.start_time, low.start_time),
        end_time=max(high.end_time, low.end_time),
        detected_time=max(high.confirmation_time or high.detected_time, low.confirmation_time or low.detected_time),
        confirmation_time=max(high.confirmation_time or high.detected_time, low.confirmation_time or low.detected_time),
        price_low=range_low,
        price_high=range_high,
        direction=direction,
        status=ConceptStatus.ACTIVE,
        quality_score=0.7,
        configuration_version=config.version,
        configuration_hash=config.configuration_hash(),
        source_dataset_id=source_dataset_id,
        supporting_bar_indexes=[low.bar_index, high.bar_index],
        supporting_event_ids=[low.id, high.id],
        high_swing_id=high.id,
        low_swing_id=low.id,
        equilibrium=(range_low + range_high) / Decimal("2"),
        normalized_current_position=current_position,
    )
    zones = [
        PremiumDiscountZone(
            id=stable_id("pd", rng.id, name),
            symbol=symbol,
            timeframe=timeframe,
            start_time=rng.start_time,
            end_time=rng.end_time,
            detected_time=rng.detected_time,
            confirmation_time=rng.confirmation_time,
            price_low=lower,
            price_high=upper,
            direction=Direction.NEUTRAL,
            status=ConceptStatus.ACTIVE,
            quality_score=0.7,
            configuration_version=config.version,
            configuration_hash=config.configuration_hash(),
            source_dataset_id=source_dataset_id,
            supporting_event_ids=[rng.id],
            range_id=rng.id,
            zone_name=name,
            lower_bound=lower,
            upper_bound=upper,
        )
        for name, lower, upper in [
            ("discount", range_low, rng.equilibrium),
            ("equilibrium", rng.equilibrium, rng.equilibrium),
            ("premium", rng.equilibrium, range_high),
            ("ote", range_low + span * Decimal(str(config.dealing_range.ote_lower_retracement)), range_low + span * Decimal(str(config.dealing_range.ote_upper_retracement))),
        ]
    ]
    return [rng], zones

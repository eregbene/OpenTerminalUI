from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, ImbalanceZone, stable_id


def detect_fair_value_gaps(
    bars: list[StructureBar],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[ImbalanceZone]:
    if not config.fvg.enabled:
        return []
    atrs = average_true_range(bars, config.displacement.atr_period)
    zones: list[ImbalanceZone] = []
    for idx in range(2, len(bars)):
        first = bars[idx - 2]
        third = bars[idx]
        atr = atrs[idx] or Decimal("0")
        candidates: list[tuple[Direction, Decimal, Decimal]] = []
        if first.high < third.low:
            candidates.append((Direction.BULLISH, first.high, third.low))
        if first.low > third.high:
            candidates.append((Direction.BEARISH, third.high, first.low))
        for direction, low, high in candidates:
            size = high - low
            if atr > 0 and size / atr < Decimal(str(config.fvg.minimum_size_atr)):
                continue
            status = ConceptStatus.ACTIVE
            mitigation_time = None
            first_mitigation_time = None
            for later in bars[idx + 1 :]:
                touched = later.low <= high and later.high >= low
                fully_filled = later.low <= low if direction == Direction.BULLISH else later.high >= high
                if touched and first_mitigation_time is None:
                    first_mitigation_time = later.close_time
                    status = ConceptStatus.PARTIAL
                if fully_filled:
                    mitigation_time = later.close_time
                    status = ConceptStatus.MITIGATED
                    break
            zones.append(
                ImbalanceZone(
                    id=stable_id("fvg", symbol, timeframe, idx, direction, low, high, config.configuration_hash()),
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=first.open_time,
                    end_time=third.close_time,
                    detected_time=third.close_time,
                    confirmation_time=third.close_time,
                    price_low=low,
                    price_high=high,
                    direction=direction,
                    status=status,
                    strength=float(size / atr) if atr > 0 else None,
                    quality_score=min(1.0, float(size / atr) if atr > 0 else 0.5),
                    configuration_version=config.version,
                    configuration_hash=config.configuration_hash(),
                    source_dataset_id=source_dataset_id,
                    supporting_bar_indexes=[idx - 2, idx - 1, idx],
                    mitigation_time=mitigation_time,
                    metadata={"variant": "three_candle_wick_gap"},
                    midpoint=(low + high) / Decimal("2"),
                    first_mitigation_time=first_mitigation_time,
                    consequent_encroachment=(low + high) / Decimal("2"),
                )
            )
    return zones

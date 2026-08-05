from __future__ import annotations

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, SwingPoint, TrendLabel, TrendState, stable_id


def classify_trend(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> TrendState:
    highs = [s for s in swings if s.swing_type == "high"]
    lows = [s for s in swings if s.swing_type == "low"]
    evidence: list[str] = []
    state = TrendLabel.UNKNOWN
    direction = Direction.UNKNOWN
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        evidence.append(f"latest highs: {highs[-2].price} -> {highs[-1].price}")
        evidence.append(f"latest lows: {lows[-2].price} -> {lows[-1].price}")
        if hh and hl:
            state = TrendLabel.BULLISH
            direction = Direction.BULLISH
            evidence.append("higher high and higher low sequence")
        elif lh and ll:
            state = TrendLabel.BEARISH
            direction = Direction.BEARISH
            evidence.append("lower high and lower low sequence")
        elif (hh and ll) or (lh and hl):
            state = TrendLabel.TRANSITIONAL
            direction = Direction.NEUTRAL
            evidence.append("mixed swing sequence")
        else:
            state = TrendLabel.RANGING
            direction = Direction.NEUTRAL
            evidence.append("no directional swing expansion")
    elif swings:
        state = TrendLabel.UNKNOWN
        evidence.append("insufficient confirmed swing sequence")
    else:
        evidence.append("no confirmed swings")

    end = bars[-1].close_time if bars else swings[-1].confirmation_time
    start = bars[0].open_time if bars else swings[0].start_time
    return TrendState(
        id=stable_id("trend", symbol, timeframe, end, config.configuration_hash()),
        symbol=symbol,
        timeframe=timeframe,
        start_time=start,
        end_time=end,
        detected_time=end,
        confirmation_time=end,
        direction=direction,
        status=ConceptStatus.CONFIRMED,
        quality_score=0.8 if state not in {TrendLabel.UNKNOWN, TrendLabel.TRANSITIONAL} else 0.4,
        configuration_version=config.version,
        configuration_hash=config.configuration_hash(),
        source_dataset_id=source_dataset_id,
        state=state,
        evidence=evidence,
    )

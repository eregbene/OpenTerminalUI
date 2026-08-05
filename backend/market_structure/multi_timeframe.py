from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.market_structure.models import Direction, MarketStructureSnapshot, TrendLabel


@dataclass(frozen=True)
class MultiTimeframeRelationship:
    lower_timeframe: str
    higher_timeframe: str
    as_of: datetime
    alignment: str
    higher_snapshot_id: str | None
    evidence: tuple[str, ...]


def align_timeframes(lower: MarketStructureSnapshot, higher: MarketStructureSnapshot | None) -> MultiTimeframeRelationship:
    as_of = lower.analysis_timestamp
    if higher is None or higher.analysis_timestamp > as_of:
        return MultiTimeframeRelationship(lower.timeframe, higher.timeframe if higher else "", as_of, "insufficient_data", None, ("no completed higher timeframe snapshot as-of event time",))
    lower_state = lower.trend.state if lower.trend else TrendLabel.UNKNOWN
    higher_state = higher.trend.state if higher.trend else TrendLabel.UNKNOWN
    if lower_state == higher_state == TrendLabel.BULLISH:
        alignment = "aligned_bullish"
    elif lower_state == higher_state == TrendLabel.BEARISH:
        alignment = "aligned_bearish"
    elif TrendLabel.UNKNOWN in {lower_state, higher_state}:
        alignment = "neutral"
    else:
        alignment = "conflicting"
    return MultiTimeframeRelationship(lower.timeframe, higher.timeframe, as_of, alignment, higher.snapshot_id, (f"lower={lower_state}", f"higher={higher_state}"))

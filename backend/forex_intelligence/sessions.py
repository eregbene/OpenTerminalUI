from __future__ import annotations

from datetime import datetime

from backend.market_structure.bar_utils import StructureBar


def session_name(timestamp: datetime) -> str:
    hour = timestamp.hour
    if 21 <= hour or hour < 6:
        return "sydney_tokyo"
    if 6 <= hour < 8:
        return "london_open"
    if 8 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_new_york_overlap"
    if 16 <= hour < 21:
        return "new_york"
    return "unknown"


def session_range(bars: list[StructureBar]) -> float | None:
    if not bars:
        return None
    current = session_name(bars[-1].close_time)
    scoped = [bar for bar in bars if session_name(bar.close_time) == current]
    if not scoped:
        return None
    return float(max(bar.high for bar in scoped) - min(bar.low for bar in scoped))

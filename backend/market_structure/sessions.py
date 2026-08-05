from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, SessionLevel, stable_id


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def detect_session_levels(
    bars: list[StructureBar],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[SessionLevel]:
    levels: list[SessionLevel] = []
    for window in config.sessions.windows:
        tz = ZoneInfo(window.timezone or config.sessions.timezone)
        selected: list[StructureBar] = []
        start_t = _parse_time(window.start)
        end_t = _parse_time(window.end)
        for bar in bars:
            local = bar.open_time.astimezone(tz)
            if local.weekday() not in window.weekdays:
                continue
            local_t = local.time()
            in_window = start_t <= local_t < end_t if start_t <= end_t else local_t >= start_t or local_t < end_t
            if in_window:
                selected.append(bar)
        if not selected:
            continue
        session_high = max(row.high for row in selected)
        session_low = min(row.low for row in selected)
        session_open = selected[0].open
        for level_name, level, direction in [
            ("high", session_high, Direction.BULLISH),
            ("low", session_low, Direction.BEARISH),
            ("open", session_open, Direction.NEUTRAL),
        ]:
            levels.append(
                SessionLevel(
                    id=stable_id("sess", symbol, timeframe, window.name, level_name, selected[0].open_time.date(), config.configuration_hash()),
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=selected[0].open_time,
                    end_time=selected[-1].close_time,
                    detected_time=selected[-1].close_time,
                    confirmation_time=selected[-1].close_time,
                    price_low=level,
                    price_high=level,
                    direction=direction,
                    status=ConceptStatus.ACTIVE,
                    quality_score=0.6,
                    configuration_version=config.version,
                    configuration_hash=config.configuration_hash(),
                    source_dataset_id=source_dataset_id,
                    supporting_bar_indexes=[row.index for row in selected],
                    session_name=window.name,
                    level_name=level_name,
                    level=level,
                )
            )
    if bars:
        grouped: dict[tuple[int, int, int], list[StructureBar]] = {}
        for bar in bars:
            grouped.setdefault((bar.open_time.year, bar.open_time.month, bar.open_time.day), []).append(bar)
        days = sorted(grouped)
        if len(days) >= 2:
            prev = grouped[days[-2]]
            for level_name, level in [("previous_day_high", max(b.high for b in prev)), ("previous_day_low", min(b.low for b in prev)), ("daily_open", grouped[days[-1]][0].open)]:
                levels.append(
                    SessionLevel(
                        id=stable_id("sessref", symbol, timeframe, level_name, bars[-1].close_time, config.configuration_hash()),
                        symbol=symbol,
                        timeframe=timeframe,
                        start_time=prev[0].open_time,
                        end_time=bars[-1].close_time,
                        detected_time=bars[-1].close_time,
                        confirmation_time=bars[-1].close_time,
                        price_low=level,
                        price_high=level,
                        direction=Direction.NEUTRAL,
                        status=ConceptStatus.ACTIVE,
                        quality_score=0.65,
                        configuration_version=config.version,
                        configuration_hash=config.configuration_hash(),
                        source_dataset_id=source_dataset_id,
                        session_name="reference",
                        level_name=level_name,
                        level=level,
                    )
                )
    return levels

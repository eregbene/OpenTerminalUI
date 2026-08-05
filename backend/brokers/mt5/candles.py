from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Candle


TIMEFRAME_NAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "DAILY": "TIMEFRAME_D1",
}

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "DAILY": 86400,
}


def mt5_timeframe(mt5: Any, timeframe: str) -> int:
    name = TIMEFRAME_NAMES.get(timeframe.upper())
    if not name or not hasattr(mt5, name):
        raise ValueError(f"unsupported MT5 timeframe: {timeframe}")
    return int(getattr(mt5, name))


def candle_from_raw(symbol: str, timeframe: str, row: Any, *, server: str | None = None, complete_override: bool | None = None) -> MT5Candle:
    data = row if isinstance(row, dict) else {key: row[key] for key in row.dtype.names}
    normalized_timeframe = timeframe.upper()
    open_time = datetime.fromtimestamp(int(data["time"]), tz=timezone.utc)
    close_time = open_time + timedelta(seconds=TIMEFRAME_SECONDS.get(normalized_timeframe, 900))
    now = datetime.now(timezone.utc)
    return MT5Candle(
        symbol=symbol,
        timeframe=normalized_timeframe,
        time=open_time,
        open=Decimal(str(data["open"])),
        high=Decimal(str(data["high"])),
        low=Decimal(str(data["low"])),
        close=Decimal(str(data["close"])),
        tick_volume=int(data.get("tick_volume", 0)),
        spread=int(data.get("spread", 0)),
        real_volume=int(data.get("real_volume", 0)),
        close_time=close_time,
        complete=complete_override if complete_override is not None else close_time <= now,
        server=server,
        ingestion_timestamp=now,
        quality_flags=[] if (complete_override if complete_override is not None else close_time <= now) else ["INCOMPLETE"],
    )

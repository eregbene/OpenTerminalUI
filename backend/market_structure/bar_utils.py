from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from backend.market_data.models import OHLCVBar


@dataclass(frozen=True)
class StructureBar:
    index: int
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    is_complete: bool = True


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("bar timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def normalize_bars(rows: Iterable[OHLCVBar | dict[str, Any]], *, symbol: str, timeframe: str) -> list[StructureBar]:
    bars: list[StructureBar] = []
    for idx, row in enumerate(rows):
        if isinstance(row, OHLCVBar):
            bars.append(
                StructureBar(
                    index=idx,
                    symbol=row.symbol or symbol,
                    timeframe=row.timeframe or timeframe,
                    open_time=_utc(row.open_time),
                    close_time=_utc(row.close_time),
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    is_complete=row.is_complete,
                )
            )
            continue
        ts = row.get("open_time") or row.get("timestamp") or row.get("time") or row.get("t")
        if isinstance(ts, (int, float)):
            open_time = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        elif isinstance(ts, str):
            open_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, datetime):
            open_time = ts
        else:
            raise ValueError("bar timestamp is required")
        open_time = _utc(open_time)
        close_time_raw = row.get("close_time")
        if isinstance(close_time_raw, str):
            close_time = datetime.fromisoformat(close_time_raw.replace("Z", "+00:00"))
        elif isinstance(close_time_raw, datetime):
            close_time = close_time_raw
        else:
            close_time = open_time + _timeframe_delta(timeframe)
        bars.append(
            StructureBar(
                index=idx,
                symbol=str(row.get("symbol") or symbol),
                timeframe=str(row.get("timeframe") or timeframe),
                open_time=open_time,
                close_time=_utc(close_time),
                open=_dec(row.get("open", row.get("o"))),
                high=_dec(row.get("high", row.get("h"))),
                low=_dec(row.get("low", row.get("l"))),
                close=_dec(row.get("close", row.get("c"))),
                volume=_dec(row.get("volume", row.get("v"))) if row.get("volume", row.get("v")) is not None else None,
                is_complete=bool(row.get("is_complete", True)),
            )
        )
    bars.sort(key=lambda bar: (bar.open_time, bar.index))
    return [StructureBar(**{**bar.__dict__, "index": idx}) for idx, bar in enumerate(bars)]


def _timeframe_delta(timeframe: str) -> timedelta:
    tf = timeframe.lower()
    if tf.endswith("m"):
        return timedelta(minutes=int(tf[:-1] or "1"))
    if tf.endswith("h"):
        return timedelta(hours=int(tf[:-1] or "1"))
    if tf.endswith("d"):
        return timedelta(days=int(tf[:-1] or "1"))
    if tf.endswith("w"):
        return timedelta(weeks=int(tf[:-1] or "1"))
    return timedelta(minutes=1)


def average_true_range(bars: list[StructureBar], period: int = 14) -> list[Decimal | None]:
    out: list[Decimal | None] = []
    trs: list[Decimal] = []
    prev_close: Decimal | None = None
    for bar in bars:
        tr = max(bar.high - bar.low, abs(bar.high - prev_close) if prev_close is not None else Decimal("0"), abs(bar.low - prev_close) if prev_close is not None else Decimal("0"))
        trs.append(tr)
        if len(trs) >= period:
            out.append(sum(trs[-period:], Decimal("0")) / Decimal(period))
        else:
            out.append(None)
        prev_close = bar.close
    return out


def safe_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)

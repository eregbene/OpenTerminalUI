from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5HistoryItem


def history_item_from_raw(raw: Any) -> MT5HistoryItem:
    data = raw._asdict() if hasattr(raw, "_asdict") else dict(raw)
    return MT5HistoryItem(
        ticket=int(data.get("ticket") or 0),
        order=int(data.get("order")) if data.get("order") is not None else None,
        position_id=int(data.get("position_id")) if data.get("position_id") is not None else None,
        symbol=data.get("symbol"),
        type=data.get("type"),
        volume=Decimal(str(data.get("volume"))) if data.get("volume") is not None else None,
        price=Decimal(str(data.get("price"))) if data.get("price") is not None else None,
        profit=Decimal(str(data.get("profit"))) if data.get("profit") is not None else None,
        commission=Decimal(str(data.get("commission"))) if data.get("commission") is not None else None,
        swap=Decimal(str(data.get("swap"))) if data.get("swap") is not None else None,
        fee=Decimal(str(data.get("fee"))) if data.get("fee") is not None else None,
        comment=data.get("comment"),
        time=_time(data.get("time")),
    )


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromtimestamp(int(value), tz=timezone.utc)
    now = datetime.now(timezone.utc)
    if parsed <= now + timedelta(minutes=5):
        return parsed
    local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    adjusted = parsed - local_offset
    return adjusted if adjusted <= now + timedelta(minutes=5) else parsed

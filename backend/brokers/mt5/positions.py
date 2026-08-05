from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Position


def position_from_raw(raw: Any) -> MT5Position:
    data = raw._asdict() if hasattr(raw, "_asdict") else dict(raw)
    return MT5Position(
        ticket=int(data.get("ticket") or 0),
        symbol=str(data.get("symbol") or ""),
        type=int(data.get("type") or 0),
        volume=Decimal(str(data.get("volume") or "0")),
        price_open=Decimal(str(data.get("price_open") or "0")),
        price_current=Decimal(str(data.get("price_current"))) if data.get("price_current") is not None else None,
        sl=Decimal(str(data.get("sl"))) if data.get("sl") is not None else None,
        tp=Decimal(str(data.get("tp"))) if data.get("tp") is not None else None,
        profit=Decimal(str(data.get("profit"))) if data.get("profit") is not None else None,
        swap=Decimal(str(data.get("swap"))) if data.get("swap") is not None else None,
        commission=Decimal(str(data.get("commission"))) if data.get("commission") is not None else None,
        magic=int(data.get("magic")) if data.get("magic") is not None else None,
        comment=str(data.get("comment")) if data.get("comment") is not None else None,
        identifier=int(data.get("identifier")) if data.get("identifier") is not None else None,
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

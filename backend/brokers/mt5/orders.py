from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation
from backend.brokers.mt5.models import MT5Order


def order_from_raw(raw: Any) -> MT5Order:
    data = raw._asdict() if hasattr(raw, "_asdict") else dict(raw)
    return MT5Order(
        ticket=int(data.get("ticket") or 0),
        symbol=str(data.get("symbol") or ""),
        type=int(data.get("type") or 0),
        volume_current=Decimal(str(data.get("volume_current") or data.get("volume_initial") or "0")),
        price_open=Decimal(str(data.get("price_open"))) if data.get("price_open") is not None else None,
        sl=Decimal(str(data.get("sl"))) if data.get("sl") is not None else None,
        tp=Decimal(str(data.get("tp"))) if data.get("tp") is not None else None,
        magic=int(data.get("magic")) if data.get("magic") is not None else None,
        comment=str(data.get("comment")) if data.get("comment") is not None else None,
        state=int(data.get("state")) if data.get("state") is not None else None,
        time_setup=_time(data.get("time_setup")),
    )


def order_send(*args, **kwargs):
    raise MT5ReadOnlyViolation()


def _time(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None

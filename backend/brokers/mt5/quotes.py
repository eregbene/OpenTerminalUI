from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Quote


def quote_from_tick(symbol: str, tick: Any) -> MT5Quote:
    data = tick._asdict() if hasattr(tick, "_asdict") else dict(tick)
    bid = _dec_or_none(data.get("bid"))
    ask = _dec_or_none(data.get("ask"))
    return MT5Quote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=_dec_or_none(data.get("last")),
        spread=(ask - bid) if ask is not None and bid is not None else None,
        time=_time(data.get("time")),
    )


def _time(value: Any) -> datetime | None:
    if value in {None, 0}:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))

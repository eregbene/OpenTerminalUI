from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from backend.brokers.ibkr.market_data import quote_for
from backend.brokers.models import BrokerBar, DataQuality, MarketDataMode, now_utc


SUPPORTED_BAR_SIZES = {"1 min", "5 mins", "15 mins", "1 hour", "1 day"}


def historical_bars(instrument_id: str, *, bar_size: str, duration: str) -> list[BrokerBar]:
    if bar_size not in SUPPORTED_BAR_SIZES:
        raise ValueError("unsupported bar size")
    quote = quote_for(instrument_id)
    step = timedelta(minutes=15 if bar_size == "15 mins" else 5 if bar_size == "5 mins" else 1)
    end = now_utc().replace(second=0, microsecond=0)
    bars: list[BrokerBar] = []
    base = quote.last or Decimal("100")
    for idx in range(64):
        ts = end - step * (64 - idx)
        close = base + Decimal(idx) * Decimal("0.01")
        bars.append(BrokerBar(instrument_id=instrument_id, timestamp=ts, open=close - Decimal("0.02"), high=close + Decimal("0.04"), low=close - Decimal("0.05"), close=close, volume=Decimal(1000 + idx), mode=MarketDataMode.DELAYED, quality=DataQuality.DELAYED))
    return bars

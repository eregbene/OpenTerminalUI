from __future__ import annotations

from decimal import Decimal

from backend.brokers.ibkr.contracts import resolve_contract
from backend.brokers.models import BrokerQuote, DataQuality, MarketDataMode


def quote_for(instrument_id: str, mode: str = "DELAYED") -> BrokerQuote:
    contract = resolve_contract(instrument_id)
    market_mode = MarketDataMode(mode) if mode in MarketDataMode.__members__ else MarketDataMode.DELAYED
    base = Decimal(str((contract.con_id % 10_000) / 100 + 50))
    delayed = market_mode in {MarketDataMode.DELAYED, MarketDataMode.DELAYED_FROZEN}
    return BrokerQuote(
        instrument_id=instrument_id,
        bid=base - Decimal("0.01"),
        ask=base + Decimal("0.01"),
        last=base,
        midpoint=base,
        volume=Decimal("1000"),
        mode=market_mode,
        quality=DataQuality.DELAYED if delayed else DataQuality.VALID,
    )

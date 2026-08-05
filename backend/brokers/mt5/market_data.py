from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.brokers.mt5.models import MT5Candle, MT5Quote, MT5Symbol


def quote_quality(symbol: MT5Symbol, quote: MT5Quote | None, *, max_age_seconds: int = 300) -> list[str]:
    reasons: list[str] = []
    if quote is None:
        return ["NO_QUOTE"]
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0:
        reasons.append("NO_QUOTE")
    if quote.bid is not None and quote.ask is not None and quote.ask < quote.bid:
        reasons.append("DATA_QUALITY_FAILED")
    if quote.time is None or (datetime.now(timezone.utc) - quote.time).total_seconds() > max_age_seconds:
        reasons.append("STALE_QUOTE")
    if quote.spread is None or quote.spread < 0:
        reasons.append("DATA_QUALITY_FAILED")
    if symbol.spread is not None and symbol.spread > 100:
        reasons.append("SPREAD_TOO_WIDE")
    return reasons


def candle_quality(rows: list[MT5Candle], required: int = 100) -> list[str]:
    reasons: list[str] = []
    completed = [row for row in rows if row.complete]
    if len(completed) < required:
        reasons.append("HISTORY_UNAVAILABLE")
    if any(row.high < row.low or row.open <= Decimal("0") or row.close <= Decimal("0") for row in completed):
        reasons.append("DATA_QUALITY_FAILED")
    return reasons


def completed_only(rows: list[MT5Candle]) -> list[MT5Candle]:
    return [row for row in rows if row.complete]

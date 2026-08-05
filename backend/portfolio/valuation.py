from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.portfolio.accounting import PositionLot


@dataclass(frozen=True)
class Mark:
  symbol: str
  price: float | None
  currency: str = "USD"
  source: str = "internal"
  mode: str = "last"
  timestamp: datetime | None = None
  quality: float = 1.0
  fallback_reason: str | None = None


def value_positions(
  positions: dict[str, PositionLot],
  marks: dict[str, Mark],
  fx_rates: dict[tuple[str, str], float],
  base_currency: str,
  stale_after_seconds: int = 900,
) -> dict:
  now = datetime.now(timezone.utc)
  rows = []
  status = "COMPLETE"
  total = 0.0
  unrealized = 0.0
  for symbol, position in positions.items():
    mark = marks.get(symbol)
    if not mark or mark.price is None:
      status = "MISSING_MARK"
      rows.append({"symbol": symbol, "quantity": position.quantity, "valuation_status": "MISSING_MARK"})
      continue
    mark_ts = mark.timestamp or now
    age = max(0.0, (now - mark_ts).total_seconds())
    row_status = "COMPLETE"
    if age > stale_after_seconds or mark.fallback_reason:
      row_status = "STALE"
      if status == "COMPLETE":
        status = "STALE"
    rate = 1.0
    if mark.currency != base_currency:
      rate = fx_rates.get((mark.currency, base_currency), 0.0)
      if rate <= 0:
        row_status = "MISSING_FX_RATE"
        status = "MISSING_FX_RATE"
        rows.append({"symbol": symbol, "quantity": position.quantity, "valuation_status": row_status})
        continue
    market_value = position.quantity * mark.price * rate
    unrealized_pnl = (mark.price - position.average_cost) * position.quantity * rate
    total += market_value
    unrealized += unrealized_pnl
    rows.append({
      "symbol": symbol,
      "quantity": position.quantity,
      "average_cost": position.average_cost,
      "mark": mark.price,
      "mark_source": mark.source,
      "mark_mode": mark.mode,
      "mark_age_seconds": age,
      "currency": mark.currency,
      "fx_rate_to_base": rate,
      "market_value": round(market_value, 8),
      "unrealized_pnl": round(unrealized_pnl, 8),
      "valuation_status": row_status,
    })
  return {
    "valuation_status": status,
    "positions": rows,
    "position_value": round(total, 8),
    "unrealized_pnl": round(unrealized, 8),
    "base_currency": base_currency,
  }

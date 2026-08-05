from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Iterable


def stable_hash(payload: object) -> str:
  encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
  return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FillInput:
  order_id: str
  symbol: str
  side: str
  quantity: float
  price: float
  commission: float = 0.0
  timestamp: datetime | None = None
  strategy_id: str | None = None


@dataclass(frozen=True)
class PositionLot:
  symbol: str
  quantity: float
  average_cost: float
  realized_pnl: float
  commissions: float


def build_average_cost_positions(fills: Iterable[FillInput]) -> dict[str, PositionLot]:
  state: dict[str, dict[str, float]] = defaultdict(lambda: {"qty": 0.0, "cost": 0.0, "realized": 0.0, "commission": 0.0})
  seen_orders: set[tuple[str, str, float, float]] = set()
  for fill in fills:
    key = (fill.order_id, fill.symbol.upper(), float(fill.quantity), float(fill.price))
    if key in seen_orders:
      continue
    seen_orders.add(key)
    symbol = fill.symbol.upper()
    qty = abs(float(fill.quantity))
    price = float(fill.price)
    commission = float(fill.commission or 0.0)
    row = state[symbol]
    row["commission"] += commission
    side = fill.side.upper()
    signed_qty = qty if side in {"BUY", "COVER", "LONG"} else -qty
    if row["qty"] == 0 or (row["qty"] > 0 and signed_qty > 0) or (row["qty"] < 0 and signed_qty < 0):
      row["cost"] += signed_qty * price
      row["qty"] += signed_qty
      continue
    closing_qty = min(abs(row["qty"]), abs(signed_qty))
    avg_cost = row["cost"] / row["qty"] if row["qty"] else 0.0
    if row["qty"] > 0:
      row["realized"] += closing_qty * (price - avg_cost) - commission
    else:
      row["realized"] += closing_qty * (avg_cost - price) - commission
    row["qty"] += signed_qty
    if row["qty"] == 0:
      row["cost"] = 0.0
    else:
      row["cost"] = row["qty"] * avg_cost
  return {
    symbol: PositionLot(
      symbol=symbol,
      quantity=round(row["qty"], 8),
      average_cost=round(row["cost"] / row["qty"], 8) if row["qty"] else 0.0,
      realized_pnl=round(row["realized"], 8),
      commissions=round(row["commission"], 8),
    )
    for symbol, row in state.items()
  }


def cash_from_fills(starting_cash: float, fills: Iterable[FillInput]) -> float:
  cash = float(starting_cash)
  seen_orders: set[tuple[str, str, float, float]] = set()
  for fill in fills:
    key = (fill.order_id, fill.symbol.upper(), float(fill.quantity), float(fill.price))
    if key in seen_orders:
      continue
    seen_orders.add(key)
    qty = abs(float(fill.quantity))
    notional = qty * float(fill.price)
    side = fill.side.upper()
    cash += notional if side in {"SELL", "SHORT"} else -notional
    cash -= float(fill.commission or 0.0)
  return round(cash, 8)

#!/usr/bin/env python
"""One-time post-incident MT5 broker-history recovery (Part 7-9 of the 2026-08-07
disaster-recovery directive; see migration 0037_mt5_broker_recovered_trades /
0036_db_incident_boundary and the db_incidents table).

The internal Bensim database history for every trade before the incident is lost
(see db_incidents.data_loss), but MetaTrader5 itself still retains the broker-side
deal/order history for as far back as the terminal keeps it. This script pulls that
history via the existing MT5 bridge/adapter and persists it into
mt5_broker_recovered_trades -- a table deliberately separate from the internal
mt5_trade_records table, since recovered rows have no cycle_id/ai_decision_id/
strategy context and it would be dishonest to fabricate any.

Every recovered row is marked:
    data_origin = 'MT5_BROKER_RECOVERY'
    recovered_after_incident = true
    recovery_incident_id = 'DB_DROP_20260807'

Idempotent: re-running is safe. Rows are upserted on (account_fingerprint, deal_ticket).

Usage (inside the backend container, where the MT5 bridge is reachable):
    python -m backend.scripts.recover_mt5_broker_history --days 365
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.brokers.mt5.adapter import mt5_adapter
from backend.brokers.mt5.account_registry import fingerprint_account
from backend.brokers.mt5.orm import MT5BrokerRecoveredTradeORM
from backend.shared.db import Base, SessionLocal, engine

_DEAL_TYPE_NAMES = {0: "BUY", 1: "SELL", 2: "BALANCE", 3: "CREDIT", 4: "CHARGE", 5: "CORRECTION", 6: "BONUS", 7: "COMMISSION", 8: "COMMISSION_DAILY", 9: "COMMISSION_MONTHLY"}
_DEAL_ENTRY_NAMES = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
_ORDER_STATE_NAMES = {0: "STARTED", 1: "PLACED", 2: "CANCELED", 3: "PARTIAL", 4: "FILLED", 5: "REJECTED", 6: "EXPIRED", 7: "REQUEST_ADD", 8: "REQUEST_MODIFY", 9: "REQUEST_CANCEL"}
_REASON_NAMES = {0: "CLIENT", 1: "EXPERT", 2: "DEALER", 3: "SL", 4: "TP", 5: "SO", 6: "ROLLOVER", 7: "VMARGIN", 8: "SPLIT"}


def _utc(ts: float | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _direction_from_deal_type(deal_type: int | None) -> str | None:
    if deal_type == 0:
        return "BUY"
    if deal_type == 1:
        return "SELL"
    return None


def _row_to_dict(row: Any) -> dict[str, Any]:
    """MetaTrader5's history_*_get() returns namedtuple-like rows; ._asdict() when
    available, else best-effort attribute extraction."""
    if hasattr(row, "_asdict"):
        return dict(row._asdict())
    return {k: getattr(row, k) for k in dir(row) if not k.startswith("_") and not callable(getattr(row, k))}


async def recover(days: int) -> dict[str, int]:
    account = await mt5_adapter.mt5_account()
    fingerprint = fingerprint_account(account)
    account_fingerprint = fingerprint.fingerprint_hash

    mt5 = mt5_adapter.client.ensure_ready()
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)

    raw_deals = await asyncio.to_thread(mt5.history_deals_get, from_date, to_date)
    raw_orders = await asyncio.to_thread(mt5.history_orders_get, from_date, to_date)
    raw_deals = list(raw_deals) if raw_deals is not None else []
    raw_orders = list(raw_orders) if raw_orders is not None else []

    orders_by_ticket: dict[int, dict[str, Any]] = {}
    for row in raw_orders:
        d = _row_to_dict(row)
        ticket = d.get("ticket")
        if ticket is not None:
            orders_by_ticket[int(ticket)] = d

    Base.metadata.create_all(bind=engine, tables=[MT5BrokerRecoveredTradeORM.__table__])

    inserted = 0
    updated = 0
    with SessionLocal() as db:
        for row in raw_deals:
            d = _row_to_dict(row)
            deal_ticket = d.get("ticket")
            if deal_ticket is None:
                continue
            order_ticket = d.get("order")
            matching_order = orders_by_ticket.get(int(order_ticket)) if order_ticket else None

            existing = (
                db.query(MT5BrokerRecoveredTradeORM)
                .filter(
                    MT5BrokerRecoveredTradeORM.account_fingerprint == account_fingerprint,
                    MT5BrokerRecoveredTradeORM.deal_ticket == int(deal_ticket),
                )
                .one_or_none()
            )
            target = existing or MT5BrokerRecoveredTradeORM(
                recovery_record_id=str(uuid4()),
                account_fingerprint=account_fingerprint,
                deal_ticket=int(deal_ticket),
            )

            deal_type = d.get("type")
            target.order_ticket = int(order_ticket) if order_ticket else None
            target.position_id = int(d["position_id"]) if d.get("position_id") else None
            target.symbol = d.get("symbol") or None
            target.direction = _direction_from_deal_type(deal_type)
            target.deal_type = _DEAL_TYPE_NAMES.get(deal_type, str(deal_type) if deal_type is not None else None)
            target.entry_type = _DEAL_ENTRY_NAMES.get(d.get("entry"), str(d.get("entry")) if d.get("entry") is not None else None)
            target.volume = float(d["volume"]) if d.get("volume") is not None else None
            target.price = float(d["price"]) if d.get("price") is not None else None
            target.deal_time = _utc(d.get("time"))
            target.profit = float(d["profit"]) if d.get("profit") is not None else None
            target.commission = float(d["commission"]) if d.get("commission") is not None else None
            target.swap = float(d["swap"]) if d.get("swap") is not None else None
            target.fee = float(d["fee"]) if d.get("fee") is not None else None
            target.comment = (d.get("comment") or None)
            target.magic = int(d["magic"]) if d.get("magic") else None
            target.reason = _REASON_NAMES.get(d.get("reason"), str(d.get("reason")) if d.get("reason") is not None else None)
            target.raw_deal_payload = {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v) for k, v in d.items()}

            if matching_order:
                target.order_setup_time = _utc(matching_order.get("time_setup"))
                target.order_done_time = _utc(matching_order.get("time_done"))
                target.stop_loss = float(matching_order["sl"]) if matching_order.get("sl") else None
                target.take_profit = float(matching_order["tp"]) if matching_order.get("tp") else None
                target.broker_status = _ORDER_STATE_NAMES.get(matching_order.get("state"), str(matching_order.get("state")) if matching_order.get("state") is not None else None)
                if not target.comment:
                    target.comment = matching_order.get("comment") or None
                if not target.magic:
                    target.magic = int(matching_order["magic"]) if matching_order.get("magic") else None
                target.raw_order_payload = {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v) for k, v in matching_order.items()}

            target.data_origin = "MT5_BROKER_RECOVERY"
            target.recovered_after_incident = True
            target.recovery_incident_id = "DB_DROP_20260807"

            if existing:
                updated += 1
            else:
                db.add(target)
                inserted += 1
        db.commit()

    return {
        "account_fingerprint": account_fingerprint,
        "deals_seen": len(raw_deals),
        "orders_seen": len(raw_orders),
        "inserted": inserted,
        "updated": updated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365, help="How many days of broker history to pull (default: 365)")
    args = parser.parse_args()

    result = asyncio.run(recover(args.days))
    print("MT5 broker-history recovery complete:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

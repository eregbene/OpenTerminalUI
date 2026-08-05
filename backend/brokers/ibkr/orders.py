from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from backend.brokers.errors import BrokerSafetyError
from backend.brokers.idempotency import idempotency_store
from backend.brokers.models import BrokerExecution, BrokerOrder, BrokerOrderCommand, BrokerOrderReceipt, BrokerOrderState, now_utc


class IBKROrderBook:
    def __init__(self) -> None:
        self.orders: dict[str, BrokerOrder] = {}
        self.executions: list[BrokerExecution] = []
        self.sequence = 1000

    def submit(self, command: BrokerOrderCommand, *, paper_verified: bool, order_enabled: bool) -> BrokerOrderReceipt:
        if not paper_verified:
            raise BrokerSafetyError("BROKER_ENVIRONMENT_UNVERIFIED", "paper account is not verified", status_code=403)
        if not order_enabled:
            raise BrokerSafetyError("BROKER_ORDER_SUBMISSION_DISABLED", "broker paper order submission disabled", status_code=403)
        if command.quantity <= 0 or command.quantity != command.approved_quantity:
            raise BrokerSafetyError("BROKER_ORDER_QUANTITY_MISMATCH", "order quantity does not match approved risk quantity", status_code=422)
        payload_hash = hashlib.sha256(json.dumps(command.model_dump(mode="json"), sort_keys=True).encode("utf-8")).hexdigest()
        idempotency_store.reserve(command.idempotency_key, payload_hash)
        self.sequence += 1
        broker_order_id = str(self.sequence)
        order = BrokerOrder(
            canonical_order_id=command.canonical_order_id,
            broker_order_id=broker_order_id,
            permanent_id=f"perm_{broker_order_id}",
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            state=BrokerOrderState.SUBMITTED,
            raw_status="Submitted",
            submitted_at=now_utc(),
            remaining_quantity=command.quantity,
            correlation_id=command.correlation_id,
        )
        execution = BrokerExecution(
            canonical_order_id=command.canonical_order_id,
            broker_order_id=broker_order_id,
            account_id=command.account_id,
            instrument_id=command.instrument_id,
            side=command.side,
            quantity=command.quantity,
            price=command.limit_price or command.stop_price or Decimal("100"),
            commission=Decimal("1.00"),
        )
        order.state = BrokerOrderState.FILLED
        order.raw_status = "Filled"
        order.filled_quantity = execution.quantity
        order.remaining_quantity = Decimal("0")
        order.average_price = execution.price
        self.orders[command.canonical_order_id] = order
        self.executions.append(execution)
        idempotency_store.finalize(command.idempotency_key, broker_order_id)
        return BrokerOrderReceipt(order=order, execution=execution)

    def open_orders(self, account_id: str) -> list[BrokerOrder]:
        return [row for row in self.orders.values() if row.account_id == account_id and row.state not in {BrokerOrderState.FILLED, BrokerOrderState.CANCELLED, BrokerOrderState.REJECTED}]

    def executions_for(self, account_id: str) -> list[BrokerExecution]:
        return [row for row in self.executions if row.account_id == account_id]


order_book = IBKROrderBook()

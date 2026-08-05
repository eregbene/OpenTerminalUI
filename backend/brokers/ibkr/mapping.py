from __future__ import annotations

from backend.trading.models import OrderStatus
from backend.brokers.models import BrokerOrderState


IBKR_TO_CANONICAL = {
    "ApiPending": BrokerOrderState.SUBMITTING,
    "PendingSubmit": BrokerOrderState.SUBMITTING,
    "PreSubmitted": BrokerOrderState.PRESUBMITTED,
    "Submitted": BrokerOrderState.SUBMITTED,
    "Filled": BrokerOrderState.FILLED,
    "Cancelled": BrokerOrderState.CANCELLED,
    "Inactive": BrokerOrderState.INACTIVE,
}


BROKER_TO_OMS = {
    BrokerOrderState.SUBMITTED: OrderStatus.ACKNOWLEDGED,
    BrokerOrderState.PRESUBMITTED: OrderStatus.ACKNOWLEDGED,
    BrokerOrderState.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    BrokerOrderState.FILLED: OrderStatus.FILLED,
    BrokerOrderState.CANCELLED: OrderStatus.CANCELLED,
    BrokerOrderState.REJECTED: OrderStatus.REJECTED,
    BrokerOrderState.ERROR: OrderStatus.ERROR,
}

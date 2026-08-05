from __future__ import annotations

from backend.trading.models import OrderStatus


VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.PENDING_RISK, OrderStatus.RISK_REJECTED, OrderStatus.APPROVED, OrderStatus.REJECTED, OrderStatus.ERROR},
    OrderStatus.PENDING_RISK: {OrderStatus.RISK_REJECTED, OrderStatus.APPROVED, OrderStatus.ERROR},
    OrderStatus.RISK_REJECTED: set(),
    OrderStatus.APPROVED: {OrderStatus.QUEUED, OrderStatus.SUBMITTED_TO_SIMULATOR, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED},
    OrderStatus.QUEUED: {OrderStatus.SUBMITTED_TO_SIMULATOR, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED},
    OrderStatus.SUBMITTED_TO_SIMULATOR: {OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED, OrderStatus.ERROR},
    OrderStatus.ACKNOWLEDGED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.EXPIRED},
    OrderStatus.CANCEL_PENDING: {OrderStatus.CANCELLED, OrderStatus.ERROR},
    OrderStatus.REPLACE_PENDING: {OrderStatus.REPLACED, OrderStatus.ERROR},
    OrderStatus.REPLACED: {OrderStatus.QUEUED, OrderStatus.SUBMITTED_TO_SIMULATOR},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.ERROR: set(),
}


def assert_transition(current: OrderStatus, target: OrderStatus) -> None:
    if target == current:
        return
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid order transition: {current.value} -> {target.value}")

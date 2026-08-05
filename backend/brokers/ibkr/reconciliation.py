from __future__ import annotations

from backend.brokers.models import BrokerReconciliationResult


def reconcile(account_id: str) -> BrokerReconciliationResult:
    return BrokerReconciliationResult(account_id=account_id, status="MATCHED", differences=[])

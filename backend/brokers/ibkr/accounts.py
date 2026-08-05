from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.brokers.models import BrokerAccount, BrokerAccountSnapshot, BrokerCashBalance, BrokerEnvironment


def discover_accounts(config: IBKRConfiguration) -> list[BrokerAccount]:
    return [
        BrokerAccount(
            account_id=account_id,
            alias=_alias(account_id),
            environment=BrokerEnvironment.PAPER if account_id.upper().startswith("DU") else BrokerEnvironment.LIVE,
            paper_verified=account_id.upper().startswith("DU") and config.expected_environment == "PAPER",
            allowed=account_id in config.account_allow_list,
            base_currency="USD",
        )
        for account_id in config.account_allow_list
    ]


def snapshot(account_id: str, config: IBKRConfiguration):
    account = next((row for row in discover_accounts(config) if row.account_id == account_id), None)
    if not account:
        raise KeyError(account_id)
    snap = BrokerAccountSnapshot(
        account_id=account.account_id,
        environment=account.environment,
        cash=[BrokerCashBalance(currency="USD", settled_cash=Decimal("100000"), available_cash=Decimal("100000"))],
    )
    raw = json.dumps(snap.model_dump(mode="json"), sort_keys=True)
    snap.content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return snap


def _alias(account_id: str) -> str:
    return f"{account_id[:2]}***{account_id[-2:]}" if len(account_id) > 4 else "***"

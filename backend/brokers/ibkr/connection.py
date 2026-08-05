from __future__ import annotations

from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.brokers.sessions import BrokerSession
from backend.brokers.models import BrokerEnvironment


def new_session() -> BrokerSession:
    return BrokerSession("ibkr", BrokerEnvironment.UNVERIFIED)


def verify_paper(config: IBKRConfiguration) -> bool:
    return config.expected_environment == "PAPER" and bool(config.account_allow_list) and all(account.upper().startswith("DU") for account in config.account_allow_list) and not config.live_trading_enabled

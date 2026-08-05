from __future__ import annotations

from backend.brokers.errors import BrokerCapabilityError, BrokerError, BrokerSafetyError


class MT5Error(BrokerError):
    pass


class MT5UnavailableError(MT5Error):
    def __init__(self, message: str = "MetaTrader5 package or terminal is unavailable") -> None:
        super().__init__("MT5_UNAVAILABLE", message, status_code=503)


class MT5ReadOnlyViolation(BrokerSafetyError):
    def __init__(self, message: str = "MT5 adapter is read-only in this phase") -> None:
        super().__init__("MT5_READ_ONLY", message, status_code=403)


class MT5CapabilityError(BrokerCapabilityError):
    pass

from __future__ import annotations

from backend.brokers.errors import BrokerCapabilityError, BrokerError, BrokerSafetyError


class CTraderError(BrokerError):
    pass


class CTraderUnavailableError(CTraderError):
    def __init__(self, message: str = "cTrader Open API connection is unavailable") -> None:
        super().__init__("CTRADER_UNAVAILABLE", message, status_code=503)


class CTraderAuthError(CTraderError):
    def __init__(self, message: str = "cTrader Open API authentication failed") -> None:
        super().__init__("CTRADER_AUTH_FAILED", message, status_code=401)


class CTraderReadOnlyViolation(BrokerSafetyError):
    def __init__(self, message: str = "cTrader adapter is read-only in this phase") -> None:
        super().__init__("CTRADER_READ_ONLY", message, status_code=403)


class CTraderEnvironmentMismatchError(BrokerSafetyError):
    """Raised at connect() time when CTRADER_ENVIRONMENT and the actually-connected account's
    real identity disagree (e.g. CTRADER_ENVIRONMENT=demo but the account/host resolves to
    live) -- refuses to proceed rather than silently trusting configuration over reality."""

    def __init__(self, message: str = "cTrader environment/account identity mismatch") -> None:
        super().__init__("CTRADER_ENVIRONMENT_MISMATCH", message, status_code=403)


class CTraderCapabilityError(BrokerCapabilityError):
    pass

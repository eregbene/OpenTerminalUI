from __future__ import annotations


class BrokerError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class BrokerCapabilityError(BrokerError):
    pass


class BrokerSafetyError(BrokerError):
    pass

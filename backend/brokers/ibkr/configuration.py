from __future__ import annotations

import os
from pydantic import BaseModel, Field, field_validator


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class IBKRConfiguration(BaseModel):
    enabled: bool = Field(default_factory=lambda: _bool_env("IBKR_ENABLED", "1"))
    mode: str = Field(default_factory=lambda: os.getenv("IBKR_MODE", "FIXTURE").strip().upper())
    host: str = Field(default_factory=lambda: os.getenv("IBKR_HOST", "host.docker.internal"))
    port: int = Field(default_factory=lambda: int(os.getenv("IBKR_PORT", "7497")))
    client_id: int = Field(default_factory=lambda: int(os.getenv("IBKR_CLIENT_ID", "111")))
    expected_account: str | None = Field(default_factory=lambda: os.getenv("IBKR_EXPECTED_ACCOUNT") or None)
    account_allow_list: list[str] = Field(default_factory=lambda: [item.strip() for item in os.getenv("IBKR_ACCOUNT_ALLOWLIST", os.getenv("IBKR_ACCOUNT_ALLOW_LIST", "DU1234567")).split(",") if item.strip()])
    expected_environment: str = Field(default_factory=lambda: os.getenv("IBKR_EXPECTED_ENVIRONMENT", "PAPER"))
    connection_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("IBKR_CONNECT_TIMEOUT_SECONDS", os.getenv("IBKR_CONNECTION_TIMEOUT_SECONDS", "5"))))
    request_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("IBKR_REQUEST_TIMEOUT_SECONDS", "10")))
    heartbeat_interval_seconds: float = Field(default_factory=lambda: float(os.getenv("IBKR_HEARTBEAT_INTERVAL_SECONDS", "30")))
    reconnect_enabled: bool = Field(default_factory=lambda: _bool_env("IBKR_RECONNECT_ENABLED", "1"))
    maximum_reconnect_attempts: int = Field(default_factory=lambda: int(os.getenv("IBKR_MAX_RECONNECT_ATTEMPTS", "3")))
    market_data_mode: str = Field(default_factory=lambda: os.getenv("IBKR_MARKET_DATA_TYPE", os.getenv("IBKR_MARKET_DATA_MODE", "DELAYED")).strip().upper())
    readonly: bool = Field(default_factory=lambda: _bool_env("IBKR_READONLY", "0"))
    order_submission_enabled: bool = Field(default_factory=lambda: _bool_env("IBKR_PAPER_ORDER_SUBMISSION_ENABLED", "0"))
    simulated: bool = Field(default_factory=lambda: _bool_env("IBKR_SIMULATED", "1"))
    live_trading_enabled: bool = False

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DISABLED", "FIXTURE", "PAPER"}:
            raise ValueError("IBKR_MODE must be DISABLED, FIXTURE, or PAPER")
        return normalized

    @property
    def adapter_mode(self) -> str:
        if self.mode == "PAPER":
            return "REAL_IBKR_PAPER"
        if self.mode == "FIXTURE":
            return "FIXTURE_IBKR"
        return "DISABLED"

    @property
    def real_paper_mode(self) -> bool:
        return self.enabled and self.mode == "PAPER"


ibkr_config = IBKRConfiguration()

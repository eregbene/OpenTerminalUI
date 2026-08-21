"""cTrader Open API configuration -- credentials come ONLY from environment/.env, never hardcoded
or committed. Mirrors brokers/mt5/config.py's mt5_config()/@dataclass pattern.

Safety (explicit user requirement): CTRADER_ENVIRONMENT must be "demo" for this phase to proceed
at all -- CTraderAdapter.connect() independently re-verifies the connected account's own reported
identity against this and refuses (CTraderEnvironmentMismatchError) rather than trusting
configuration alone. See adapter.py::_verify_environment_identity.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CTraderConfig:
    environment: str  # "demo" | "live" -- this phase refuses anything but "demo"
    client_id: str | None
    client_secret: str | None
    access_token: str | None
    refresh_token: str | None
    account_id: str | None  # cTrader's ctidTraderAccountId (numeric, as a string) -- e.g. "10102160"
    redirect_uri: str

    @property
    def has_app_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def has_account_tokens(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    @property
    def is_demo(self) -> bool:
        return self.environment.strip().lower() == "demo"


def ctrader_config() -> CTraderConfig:
    """Read live (not cached at import time) so tests can monkeypatch os.environ per-case, same
    convention as brokers/mt5/config.py::mt5_config()."""
    return CTraderConfig(
        environment=os.getenv("CTRADER_ENVIRONMENT", "demo").strip() or "demo",
        client_id=os.getenv("CTRADER_CLIENT_ID") or None,
        client_secret=os.getenv("CTRADER_CLIENT_SECRET") or None,
        access_token=os.getenv("CTRADER_ACCESS_TOKEN") or None,
        refresh_token=os.getenv("CTRADER_REFRESH_TOKEN") or None,
        account_id=os.getenv("CTRADER_ACCOUNT_ID") or None,
        redirect_uri=os.getenv("CTRADER_REDIRECT_URI", "http://localhost:5000/ctrader/oauth/callback"),
    )

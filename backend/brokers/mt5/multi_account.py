from __future__ import annotations

import os

from backend.brokers.mt5.account_registry import MT5AccountProfile, profile_by_id
from backend.brokers.mt5.adapter import MT5Adapter
from backend.brokers.mt5.config import MT5Config, mt5_config


def config_for_profile(profile: MT5AccountProfile) -> MT5Config:
    base = mt5_config()
    return MT5Config(
        **{
            **base.__dict__,
            "account_id": profile.account_id,
            "enabled": profile.enabled,
            "login": profile.expected_login,
            "server": profile.expected_server,
            "path": profile.terminal_path,
            "account_mode": profile.account_mode,
            "prop_profile": profile.prop_profile,
            "bridge_host": profile.bridge_host,
            "bridge_port": profile.bridge_port,
            "bridge_api_key": _bridge_key(profile),
            "password": _account_password(profile),
            "read_only": True,
        }
    )


def adapter_for_account(account_id: str) -> MT5Adapter:
    profile = profile_by_id(account_id)
    if profile is None:
        raise KeyError(f"unknown MT5 account_id: {account_id}")
    return MT5Adapter(config_for_profile(profile))


def _bridge_key(profile: MT5AccountProfile) -> str | None:
    suffix = profile.account_id.upper().replace("FTMO_DEMO_", "").replace("DEMO_", "")
    return os.getenv(f"MT5_ACCOUNT_{suffix}_BRIDGE_API_KEY") or os.getenv(f"MT5_{suffix}_BRIDGE_API_KEY") or os.getenv("MT5_BRIDGE_API_KEY") or None


def _account_password(profile: MT5AccountProfile) -> str | None:
    # demo_10k IS the base account -- its password is the bare MT5_PASSWORD var, nothing
    # prefixed. Every other account must NOT fall back to MT5_PASSWORD: that fallback used to
    # happen implicitly via **base.__dict__ in config_for_profile, causing every other account's
    # mt5.login() call to silently authenticate with demo_10k's password instead of its own.
    if profile.account_id == "demo_10k":
        return os.getenv("MT5_PASSWORD") or None
    suffix = profile.account_id.upper().replace("FTMO_DEMO_", "").replace("DEMO_", "")
    return os.getenv(f"MT5_ACCOUNT_{suffix}_PASSWORD") or os.getenv(f"MT5_{suffix}_PASSWORD") or None

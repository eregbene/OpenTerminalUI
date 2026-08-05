from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.models import MT5TerminalStatus


def mask_login(login: int | str | None) -> str | None:
    if login is None:
        return None
    raw = str(login)
    if len(raw) <= 4:
        return "***"
    return f"{raw[:2]}***{raw[-2:]}"


def terminal_status_from_raw(
    *,
    config: MT5Config,
    initialized: bool,
    terminal: dict[str, Any] | None,
    account: dict[str, Any] | None,
    version: tuple[int, int, str] | None,
    last_error: tuple[int, str] | None,
) -> MT5TerminalStatus:
    terminal = terminal or {}
    account = account or {}
    connected = bool(account) and bool(terminal.get("connected", True))
    trade_allowed = _bool(account.get("trade_allowed", terminal.get("trade_allowed")))
    ea_allowed = _bool(terminal.get("trade_allowed", terminal.get("tradeapi_disabled") is False))
    return MT5TerminalStatus(
        connected=connected,
        initialized=initialized,
        package_available=True,
        terminal_info=terminal,
        version=version,
        last_error=last_error,
        terminal_installed=bool(config.path) or bool(terminal),
        terminal_running=initialized,
        initialize_success=initialized,
        login_success=bool(account),
        trade_connection_available=connected,
        masked_login=mask_login(account.get("login") or config.login),
        server=account.get("server") or config.server,
        company=account.get("company"),
        currency=account.get("currency"),
        balance=account.get("balance"),
        equity=account.get("equity"),
        margin=account.get("margin"),
        free_margin=account.get("margin_free") or account.get("free_margin"),
        leverage=account.get("leverage"),
        account_trade_mode=account.get("trade_mode"),
        account_mode=config.account_mode,
        margin_mode=account.get("margin_mode"),
        hedging_mode=_hedging_mode(account.get("margin_mode")),
        trade_allowed=trade_allowed,
        ea_trading_allowed=ea_allowed,
        external_python_trading_allowed=connected,
        last_successful_heartbeat=datetime.now(timezone.utc) if connected else None,
    )


def assert_demo_account(config: MT5Config, account: dict[str, Any] | None) -> None:
    from backend.brokers.mt5.exceptions import MT5UnavailableError

    if config.account_mode != "DEMO":
        raise MT5UnavailableError("MT5_ACCOUNT_MODE must be DEMO for this read-only milestone")
    trade_mode = None if not account else account.get("trade_mode")
    server = str((account or {}).get("server") or config.server or "").upper()
    if trade_mode not in {None, 0} and "DEMO" not in server:
        raise MT5UnavailableError("connected MT5 account is not confirmed as demo")


def _hedging_mode(margin_mode: Any) -> str:
    if margin_mode in {2, "2"}:
        return "HEDGING"
    if margin_mode is None:
        return "UNKNOWN"
    return "NETTING"


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)

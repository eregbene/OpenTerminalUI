from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MT5Config:
    enabled: bool = False
    login: int | None = None
    password: str | None = None
    server: str | None = None
    path: str | None = None
    timeout_ms: int = 60000
    portable: bool = False
    account_mode: str = "DEMO"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    autonomous_submission_enabled: bool = False
    manual_acceptance_enabled: bool = False
    database_recovery_in_progress: bool = False
    broker_provider: str = "MT5"
    forex_execution_provider: str = "MT5"
    max_new_entries_per_cycle: int = 1
    bensim_magic: int = 5601001
    max_holding_minutes: int = 240
    autonomous_rollout_max_entries: int = 3
    autonomous_rollout_complete: bool = False
    rollout_max_lots: float | None = None
    rollout_max_open_positions: int | None = None
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    bridge_api_key: str | None = None
    calendar_export_file: str | None = None
    forex_universe_mode: str = "ALL_AVAILABLE"
    include_majors: bool = True
    include_minors: bool = True
    include_exotics: bool = True
    include_metals: bool = True
    excluded_symbols: tuple[str, ...] = ()
    excluded_currencies: tuple[str, ...] = ()
    excluded_group_patterns: tuple[str, ...] = ()
    trading_symbols: tuple[str, ...] = (
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "AUDUSD",
        "USDCAD",
        "USDCHF",
        "NZDUSD",
        "EURJPY",
        "GBPJPY",
        "XAUUSD",
    )
    max_ai_candidates_per_cycle: int = 1
    max_provider_requests_per_cycle: int = 1
    max_provider_requests_per_hour: int = 2
    max_provider_requests_per_day: int = 10
    daily_cost_limit_usd: float = 0.10
    monthly_cost_limit_usd: float = 4.50
    target_max_input_tokens: int = 2500
    max_output_tokens: int = 250
    block_on_context_warnings: bool = False
    block_on_calendar_unavailable: bool = False
    max_trades_per_day: int = 20
    max_trades_per_symbol_per_day: int = 3
    max_open_positions: int = 5
    max_total_open_orders: int = 15
    max_consecutive_losses: int = 5
    post_trade_cooldown_minutes: int = 15
    risk_percent_per_trade: float = 0.25
    max_risk_per_trade_usd: float = 250.0
    max_daily_loss_usd: float = 1500.0
    max_total_open_risk_usd: float = 1000.0
    internal_max_daily_loss_percent: float = 1.50
    internal_max_total_drawdown_percent: float = 5.00
    max_total_open_risk_percent: float = 1.00
    max_positions_per_base_currency: int = 3
    max_positions_per_quote_currency: int = 3
    max_correlated_positions: int = 3
    allow_opposing_same_symbol_positions: bool = False
    max_positions_per_symbol: int = 1
    prop_profile: str = "GENERIC_PROP_CONSERVATIVE"
    request_timeout_ms: int = 60000
    default_history_count: int = 100
    read_only: bool = True
    risk_calculation_disagreement_pct: float = 10.0
    risk_calculation_critical_disagreement_pct: float = 100.0


def _csv(name: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in (os.getenv(name) or "").split(",") if part.strip())


def mt5_config() -> MT5Config:
    login_raw = os.getenv("MT5_LOGIN", "").strip()
    timeout_ms = int(os.getenv("MT5_TIMEOUT_MS", os.getenv("MT5_REQUEST_TIMEOUT_MS", "60000")))
    return MT5Config(
        enabled=_bool("MT5_ENABLED", False),
        login=int(login_raw) if login_raw.isdigit() else None,
        password=os.getenv("MT5_PASSWORD") or None,
        server=os.getenv("MT5_SERVER") or None,
        path=os.getenv("MT5_PATH") or None,
        timeout_ms=timeout_ms,
        portable=_bool("MT5_PORTABLE", False),
        account_mode=(os.getenv("MT5_ACCOUNT_MODE") or "DEMO").upper(),
        live_trading_enabled=_bool("MT5_LIVE_TRADING_ENABLED", False),
        order_submission_enabled=_bool("MT5_ORDER_SUBMISSION_ENABLED", False),
        autonomous_submission_enabled=_bool("MT5_AUTONOMOUS_SUBMISSION_ENABLED", False),
        manual_acceptance_enabled=_bool("MT5_MANUAL_ACCEPTANCE_ENABLED", False),
        database_recovery_in_progress=_bool("DATABASE_RECOVERY_IN_PROGRESS", False),
        broker_provider=(os.getenv("BROKER_PROVIDER") or "MT5").upper(),
        forex_execution_provider=(os.getenv("FOREX_EXECUTION_PROVIDER") or "MT5").upper(),
        max_new_entries_per_cycle=int(os.getenv("MT5_MAX_NEW_ENTRIES_PER_CYCLE", "1")),
        bensim_magic=int(os.getenv("MT5_BENSIM_MAGIC", "5601001")),
        max_holding_minutes=int(os.getenv("MT5_MAX_HOLDING_MINUTES", "240")),
        autonomous_rollout_max_entries=int(os.getenv("MT5_AUTONOMOUS_ROLLOUT_MAX_ENTRIES", "0")),
        autonomous_rollout_complete=_bool("MT5_AUTONOMOUS_ROLLOUT_COMPLETE", True),
        rollout_max_lots=_optional_float("MT5_ROLLOUT_MAX_LOTS"),
        rollout_max_open_positions=_optional_int("MT5_ROLLOUT_MAX_OPEN_POSITIONS"),
        bridge_host=os.getenv("MT5_BRIDGE_HOST") or "127.0.0.1",
        bridge_port=int(os.getenv("MT5_BRIDGE_PORT", "8765")),
        bridge_api_key=os.getenv("MT5_BRIDGE_API_KEY") or None,
        calendar_export_file=os.getenv("MT5_CALENDAR_EXPORT_FILE") or None,
        forex_universe_mode=(os.getenv("MT5_FOREX_UNIVERSE_MODE") or "ALL_AVAILABLE").upper(),
        include_majors=_bool("MT5_INCLUDE_MAJORS", True),
        include_minors=_bool("MT5_INCLUDE_MINORS", True),
        include_exotics=_bool("MT5_INCLUDE_EXOTICS", True),
        include_metals=_bool("MT5_INCLUDE_METALS", True),
        excluded_symbols=_csv("MT5_EXCLUDED_SYMBOLS"),
        excluded_currencies=_csv("MT5_EXCLUDED_CURRENCIES"),
        excluded_group_patterns=_csv("MT5_EXCLUDED_GROUP_PATTERNS"),
        trading_symbols=_csv("MT5_TRADING_SYMBOLS")
        or (
            "EURUSD",
            "GBPUSD",
            "USDJPY",
            "AUDUSD",
            "USDCAD",
            "USDCHF",
            "NZDUSD",
            "EURJPY",
            "GBPJPY",
            "XAUUSD",
        ),
        max_ai_candidates_per_cycle=int(os.getenv("MT5_MAX_AI_CANDIDATES_PER_CYCLE", "1")),
        max_provider_requests_per_cycle=int(os.getenv("AI_MAX_PROVIDER_REQUESTS_PER_CYCLE", "1")),
        max_provider_requests_per_hour=int(os.getenv("AI_MAX_PROVIDER_REQUESTS_PER_HOUR", "2")),
        max_provider_requests_per_day=int(os.getenv("AI_MAX_PROVIDER_REQUESTS_PER_DAY", "10")),
        daily_cost_limit_usd=float(os.getenv("AI_DAILY_COST_LIMIT_USD", "0.10")),
        monthly_cost_limit_usd=float(os.getenv("AI_MONTHLY_COST_LIMIT_USD", "4.50")),
        target_max_input_tokens=int(os.getenv("AI_TARGET_MAX_INPUT_TOKENS", "2500")),
        max_output_tokens=int(os.getenv("AI_MAX_OUTPUT_TOKENS", "250")),
        block_on_context_warnings=_bool("MT5_BLOCK_ON_CONTEXT_WARNINGS", False),
        block_on_calendar_unavailable=_bool("MT5_BLOCK_ON_CALENDAR_UNAVAILABLE", False),
        max_trades_per_day=int(os.getenv("MT5_MAX_TRADES_PER_DAY", "20")),
        max_trades_per_symbol_per_day=int(os.getenv("MT5_MAX_TRADES_PER_SYMBOL_PER_DAY", "3")),
        max_open_positions=int(os.getenv("MT5_MAX_OPEN_POSITIONS", "5")),
        max_total_open_orders=int(os.getenv("MT5_MAX_TOTAL_OPEN_ORDERS", "15")),
        max_consecutive_losses=int(os.getenv("MT5_MAX_CONSECUTIVE_LOSSES", "5")),
        post_trade_cooldown_minutes=int(os.getenv("MT5_POST_TRADE_COOLDOWN_MINUTES", "15")),
        risk_percent_per_trade=float(os.getenv("MT5_RISK_PERCENT_PER_TRADE", "0.25")),
        max_risk_per_trade_usd=float(os.getenv("MT5_MAX_RISK_PER_TRADE_USD", "250")),
        max_daily_loss_usd=float(os.getenv("MT5_MAX_DAILY_LOSS_USD", "1500")),
        max_total_open_risk_usd=float(os.getenv("MT5_MAX_TOTAL_OPEN_RISK_USD", "1000")),
        internal_max_daily_loss_percent=float(os.getenv("MT5_INTERNAL_MAX_DAILY_LOSS_PERCENT", "1.50")),
        internal_max_total_drawdown_percent=float(os.getenv("MT5_INTERNAL_MAX_TOTAL_DRAWDOWN_PERCENT", "5.00")),
        max_total_open_risk_percent=float(os.getenv("MT5_MAX_TOTAL_OPEN_RISK_PERCENT", "1.00")),
        max_positions_per_base_currency=int(os.getenv("MT5_MAX_POSITIONS_PER_BASE_CURRENCY", "3")),
        max_positions_per_quote_currency=int(os.getenv("MT5_MAX_POSITIONS_PER_QUOTE_CURRENCY", "3")),
        max_correlated_positions=int(os.getenv("MT5_MAX_CORRELATED_POSITIONS", "3")),
        allow_opposing_same_symbol_positions=_bool("MT5_ALLOW_OPPOSING_SAME_SYMBOL_POSITIONS", False),
        max_positions_per_symbol=int(os.getenv("MT5_MAX_POSITIONS_PER_SYMBOL", "1")),
        prop_profile=(os.getenv("PROP_PROFILE") or "GENERIC_PROP_CONSERVATIVE").upper(),
        request_timeout_ms=timeout_ms,
        default_history_count=int(os.getenv("MT5_DEFAULT_HISTORY_COUNT", "100")),
        read_only=True,
        risk_calculation_disagreement_pct=float(os.getenv("MT5_RISK_CALCULATION_DISAGREEMENT_PCT", "10")),
        risk_calculation_critical_disagreement_pct=float(os.getenv("MT5_RISK_CALCULATION_CRITICAL_DISAGREEMENT_PCT", "100")),
    )


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)

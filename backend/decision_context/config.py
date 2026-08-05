from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


@dataclass(frozen=True)
class DecisionContextConfig:
    enabled: bool = True
    mt5_calendar_enabled: bool = True
    mt5_calendar_refresh_seconds: int = 60
    mt5_calendar_lookahead_days: int = 14
    mt5_calendar_history_days: int = 30
    mt5_calendar_stale_after_seconds: int = 180
    mt5_calendar_strict_mode: bool = True
    gdelt_enabled: bool = True
    gdelt_refresh_interval_seconds: int = 300
    gdelt_request_timeout_seconds: int = 20
    gdelt_max_results_per_query: int = 50
    alpha_vantage_enabled: bool = True
    alpha_vantage_api_key: str | None = None
    alpha_vantage_request_timeout_seconds: int = 15
    alpha_vantage_refresh_interval_seconds: int = 900
    fred_enabled: bool = True
    fred_api_key: str | None = None
    fred_request_timeout_seconds: int = 15
    fred_refresh_interval_seconds: int = 21600
    news_stale_after_minutes: int = 60
    news_dedup_window_hours: int = 72
    retention_days: int = 730
    high_impact_pre_block_minutes: int = 30
    high_impact_post_block_minutes: int = 15
    medium_impact_pre_warning_minutes: int = 20
    medium_impact_post_warning_minutes: int = 10
    news_high_risk_block_enabled: bool = True
    news_elevated_risk_ack_required: bool = True
    strict_mode: bool = True


def decision_context_config() -> DecisionContextConfig:
    return DecisionContextConfig(
        enabled=_bool("DECISION_CONTEXT_ENABLED", True),
        mt5_calendar_enabled=_bool("MT5_CALENDAR_ENABLED", True),
        mt5_calendar_refresh_seconds=_int("MT5_CALENDAR_REFRESH_SECONDS", 60),
        mt5_calendar_lookahead_days=_int("MT5_CALENDAR_LOOKAHEAD_DAYS", 14),
        mt5_calendar_history_days=_int("MT5_CALENDAR_HISTORY_DAYS", 30),
        mt5_calendar_stale_after_seconds=_int("MT5_CALENDAR_STALE_AFTER_SECONDS", 180),
        mt5_calendar_strict_mode=_bool("MT5_CALENDAR_STRICT_MODE", True),
        gdelt_enabled=_bool("GDELT_ENABLED", True),
        gdelt_refresh_interval_seconds=_int("GDELT_REFRESH_INTERVAL_SECONDS", 300),
        gdelt_request_timeout_seconds=_int("GDELT_REQUEST_TIMEOUT_SECONDS", 20),
        gdelt_max_results_per_query=_int("GDELT_MAX_RESULTS_PER_QUERY", 50),
        alpha_vantage_enabled=_bool("ALPHA_VANTAGE_ENABLED", True),
        alpha_vantage_api_key=(os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip() or None,
        alpha_vantage_request_timeout_seconds=_int("ALPHA_VANTAGE_REQUEST_TIMEOUT_SECONDS", 15),
        alpha_vantage_refresh_interval_seconds=_int("ALPHA_VANTAGE_REFRESH_INTERVAL_SECONDS", 900),
        fred_enabled=_bool("FRED_ENABLED", True),
        fred_api_key=(os.getenv("FRED_API_KEY") or "").strip() or None,
        fred_request_timeout_seconds=_int("FRED_REQUEST_TIMEOUT_SECONDS", 15),
        fred_refresh_interval_seconds=_int("FRED_REFRESH_INTERVAL_SECONDS", 21600),
        news_stale_after_minutes=_int("CONTEXT_NEWS_STALE_AFTER_MINUTES", 60),
        news_dedup_window_hours=_int("CONTEXT_NEWS_DEDUP_WINDOW_HOURS", 72),
        retention_days=_int("CONTEXT_RETENTION_DAYS", 730),
        high_impact_pre_block_minutes=_int("CALENDAR_HIGH_IMPACT_PRE_BLOCK_MINUTES", 30),
        high_impact_post_block_minutes=_int("CALENDAR_HIGH_IMPACT_POST_BLOCK_MINUTES", 15),
        medium_impact_pre_warning_minutes=_int("CALENDAR_MEDIUM_IMPACT_PRE_WARNING_MINUTES", 20),
        medium_impact_post_warning_minutes=_int("CALENDAR_MEDIUM_IMPACT_POST_WARNING_MINUTES", 10),
        news_high_risk_block_enabled=_bool("NEWS_HIGH_RISK_BLOCK_ENABLED", True),
        news_elevated_risk_ack_required=_bool("NEWS_ELEVATED_RISK_ACK_REQUIRED", True),
        strict_mode=_bool("CONTEXT_STRICT_MODE", True),
    )

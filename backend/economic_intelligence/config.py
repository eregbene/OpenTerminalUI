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


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw and raw.strip() else default
    except Exception:
        return default


def _enum(name: str, default: str, allowed: set[str]) -> str:
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in allowed else default


@dataclass(frozen=True)
class EconomicIntelligenceConfig:
    ff_calendar_enabled: bool = True
    ff_news_enabled: bool = True
    ff_event_detail_enabled: bool = True
    ff_weekly_json_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    ff_calendar_refresh_seconds: int = 300
    ff_release_poll_seconds: int = 60
    ff_news_refresh_seconds: int = 300
    ff_event_detail_stale_days: int = 30
    ff_request_timeout_seconds: int = 20
    ff_max_retries: int = 3
    ff_backfill_delay_seconds: int = 3
    ff_backfill_max_months_per_run: int = 3
    ff_provider_fail_mode: str = "conservative"
    ff_high_impact_block_before_minutes: int = 30
    ff_high_impact_delay_after_minutes: int = 15
    ff_medium_impact_block_before_minutes: int = 15
    ff_medium_impact_delay_after_minutes: int = 5
    ff_central_bank_block_before_minutes: int = 60
    ff_central_bank_delay_after_minutes: int = 30
    ff_require_spread_normalization: bool = True
    ff_require_post_event_closed_candles: int = 1
    ff_openai_macro_classification_enabled: bool = True
    ff_openai_min_confidence: float = 0.65
    ff_debug_raw_html: bool = False
    ff_calendar_stale_after_seconds: int = 900
    ff_calendar_degraded_after_seconds: int = 3600
    ff_news_dedup_window_hours: int = 72
    ff_news_max_age_minutes: int = 180
    ff_release_poll_window_minutes: int = 20
    # Hardening phase (0029)
    ff_economic_guard_mode: str = "enforce"  # disabled|shadow|enforce -- "enforce" preserves
    # today's live behavior exactly; shadow/disabled are explicit opt-in, never a silent default
    # change to existing protections.
    ff_news_selector_version: str = ""
    ff_news_min_expected_items: int = 5
    ff_news_max_expected_items: int = 500
    ff_news_min_valid_url_ratio: float = 0.90
    ff_news_max_duplicate_ratio: float = 0.25
    ff_event_detail_selector_version: str = ""
    ff_release_fast_poll_enabled: bool = True
    ff_release_fast_poll_before_minutes: int = 10
    ff_release_fast_poll_after_minutes: int = 15
    ff_release_fast_poll_seconds: int = 60
    ff_shadow_retention_days: int = 180
    ff_shadow_outcome_link_enabled: bool = True


def economic_intelligence_config() -> EconomicIntelligenceConfig:
    return EconomicIntelligenceConfig(
        ff_calendar_enabled=_bool("FF_CALENDAR_ENABLED", True),
        ff_news_enabled=_bool("FF_NEWS_ENABLED", True),
        ff_event_detail_enabled=_bool("FF_EVENT_DETAIL_ENABLED", True),
        ff_weekly_json_url=os.getenv("FF_WEEKLY_JSON_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
        ff_calendar_refresh_seconds=_int("FF_CALENDAR_REFRESH_SECONDS", 300),
        ff_release_poll_seconds=_int("FF_RELEASE_POLL_SECONDS", 60),
        ff_news_refresh_seconds=_int("FF_NEWS_REFRESH_SECONDS", 300),
        ff_event_detail_stale_days=_int("FF_EVENT_DETAIL_STALE_DAYS", 30),
        ff_request_timeout_seconds=_int("FF_REQUEST_TIMEOUT_SECONDS", 20),
        ff_max_retries=_int("FF_MAX_RETRIES", 3),
        ff_backfill_delay_seconds=_int("FF_BACKFILL_DELAY_SECONDS", 3),
        ff_backfill_max_months_per_run=_int("FF_BACKFILL_MAX_MONTHS_PER_RUN", 3),
        ff_provider_fail_mode=os.getenv("FF_PROVIDER_FAIL_MODE", "conservative").strip().lower() or "conservative",
        ff_high_impact_block_before_minutes=_int("FF_HIGH_IMPACT_BLOCK_BEFORE_MINUTES", 30),
        ff_high_impact_delay_after_minutes=_int("FF_HIGH_IMPACT_DELAY_AFTER_MINUTES", 15),
        ff_medium_impact_block_before_minutes=_int("FF_MEDIUM_IMPACT_BLOCK_BEFORE_MINUTES", 15),
        ff_medium_impact_delay_after_minutes=_int("FF_MEDIUM_IMPACT_DELAY_AFTER_MINUTES", 5),
        ff_central_bank_block_before_minutes=_int("FF_CENTRAL_BANK_BLOCK_BEFORE_MINUTES", 60),
        ff_central_bank_delay_after_minutes=_int("FF_CENTRAL_BANK_DELAY_AFTER_MINUTES", 30),
        ff_require_spread_normalization=_bool("FF_REQUIRE_SPREAD_NORMALIZATION", True),
        ff_require_post_event_closed_candles=_int("FF_REQUIRE_POST_EVENT_CLOSED_CANDLES", 1),
        ff_openai_macro_classification_enabled=_bool("FF_OPENAI_MACRO_CLASSIFICATION_ENABLED", True),
        ff_openai_min_confidence=_float("FF_OPENAI_MIN_CONFIDENCE", 0.65),
        ff_debug_raw_html=_bool("FF_DEBUG_RAW_HTML", False),
        ff_calendar_stale_after_seconds=_int("FF_CALENDAR_STALE_AFTER_SECONDS", 900),
        ff_calendar_degraded_after_seconds=_int("FF_CALENDAR_DEGRADED_AFTER_SECONDS", 3600),
        ff_news_dedup_window_hours=_int("FF_NEWS_DEDUP_WINDOW_HOURS", 72),
        ff_news_max_age_minutes=_int("FF_NEWS_MAX_AGE_MINUTES", 180),
        ff_release_poll_window_minutes=_int("FF_RELEASE_POLL_WINDOW_MINUTES", 20),
        ff_economic_guard_mode=_enum("FF_ECONOMIC_GUARD_MODE", "enforce", {"disabled", "shadow", "enforce"}),
        ff_news_selector_version=(os.getenv("FF_NEWS_SELECTOR_VERSION") or "").strip(),
        ff_news_min_expected_items=_int("FF_NEWS_MIN_EXPECTED_ITEMS", 5),
        ff_news_max_expected_items=_int("FF_NEWS_MAX_EXPECTED_ITEMS", 500),
        ff_news_min_valid_url_ratio=_float("FF_NEWS_MIN_VALID_URL_RATIO", 0.90),
        ff_news_max_duplicate_ratio=_float("FF_NEWS_MAX_DUPLICATE_RATIO", 0.25),
        ff_event_detail_selector_version=(os.getenv("FF_EVENT_DETAIL_SELECTOR_VERSION") or "").strip(),
        ff_release_fast_poll_enabled=_bool("FF_RELEASE_FAST_POLL_ENABLED", True),
        ff_release_fast_poll_before_minutes=_int("FF_RELEASE_FAST_POLL_BEFORE_MINUTES", 10),
        ff_release_fast_poll_after_minutes=_int("FF_RELEASE_FAST_POLL_AFTER_MINUTES", 15),
        ff_release_fast_poll_seconds=_int("FF_RELEASE_FAST_POLL_SECONDS", 60),
        ff_shadow_retention_days=_int("FF_SHADOW_RETENTION_DAYS", 180),
        ff_shadow_outcome_link_enabled=_bool("FF_SHADOW_OUTCOME_LINK_ENABLED", True),
    )

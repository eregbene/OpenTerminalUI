from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.persistence import all_provider_states, provider_state, save_provider_state
from backend.shared.cache import cache as shared_cache

logger = logging.getLogger(__name__)

HEALTHY = "HEALTHY"
STALE = "STALE"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
SCHEMA_CHANGED = "SCHEMA_CHANGED"

_LAST_KNOWN_GOOD_PREFIX = "ff:last_known_good:"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def cache_last_known_good(provider: str, payload: Any, *, ttl_seconds: int) -> None:
    await shared_cache.set(f"{_LAST_KNOWN_GOOD_PREFIX}{provider}", payload, ttl=ttl_seconds)


async def last_known_good(provider: str) -> Any:
    return await shared_cache.get(f"{_LAST_KNOWN_GOOD_PREFIX}{provider}")


def classify_state(*, last_successful_sync: datetime | None, schema_changed: bool, consecutive_failures: int, config: EconomicIntelligenceConfig) -> str:
    """Classify a provider into HEALTHY/STALE/DEGRADED/UNAVAILABLE/SCHEMA_CHANGED."""
    if schema_changed:
        return SCHEMA_CHANGED
    if last_successful_sync is None:
        return UNAVAILABLE
    last = last_successful_sync if last_successful_sync.tzinfo else last_successful_sync.replace(tzinfo=timezone.utc)
    age_seconds = (utcnow() - last).total_seconds()
    if consecutive_failures >= 5 or age_seconds > config.ff_calendar_degraded_after_seconds:
        return DEGRADED if consecutive_failures < 10 else UNAVAILABLE
    if age_seconds > config.ff_calendar_stale_after_seconds:
        return STALE
    return HEALTHY


def record_success(provider: str, *, records_received: int = 0, records_accepted: int | None = None, selector_version: str | None = None) -> None:
    accepted = records_received if records_accepted is None else records_accepted
    was_schema_changed = bool((provider_state(provider) or {}).get("schema_changed"))
    if was_schema_changed:
        logger.warning("Forex Factory provider %s recovered from SCHEMA_CHANGED to HEALTHY", provider)
    save_provider_state(
        provider,
        status=HEALTHY,
        last_successful_sync=utcnow(),
        last_known_good_at=utcnow(),
        last_attempt_at=utcnow(),
        consecutive_failures=0,
        schema_changed=False,
        last_failure_reason=None,
        records_accepted=accepted,
        records_rejected=max(0, records_received - accepted),
        selector_version=selector_version,
    )


def record_degraded(provider: str, *, reason: str, records_received: int = 0, records_accepted: int | None = None, selector_version: str | None = None) -> None:
    """Partial-but-usable result: valid records may still be stored (caller's responsibility),
    provider state reflects DEGRADED with a required diagnostic reason."""
    accepted = records_received if records_accepted is None else records_accepted
    save_provider_state(
        provider,
        status=DEGRADED,
        last_successful_sync=utcnow(),
        last_known_good_at=utcnow(),
        last_attempt_at=utcnow(),
        consecutive_failures=0,
        schema_changed=False,
        last_failure_reason=reason,
        records_accepted=accepted,
        records_rejected=max(0, records_received - accepted),
        selector_version=selector_version,
    )


def record_failure(provider: str, *, reason: str, schema_changed: bool = False) -> None:
    existing = provider_state(provider) or {}
    failures = int(existing.get("consecutive_failures") or 0) + 1
    retries = int(existing.get("retry_count") or 0) + 1
    save_provider_state(provider, status=SCHEMA_CHANGED if schema_changed else UNAVAILABLE, consecutive_failures=failures, retry_count=retries, last_attempt_at=utcnow(), schema_changed=schema_changed, last_failure_reason=reason)


def record_cache_event(provider: str, *, hit: bool) -> None:
    existing = provider_state(provider) or {}
    field = "cache_hits" if hit else "cache_misses"
    save_provider_state(provider, **{field: int(existing.get(field) or 0) + 1})


def record_lock_owner(provider: str, owner: str | None) -> None:
    save_provider_state(provider, lock_owner=owner)


def health_snapshot(config: EconomicIntelligenceConfig, *, next_due: dict[str, datetime] | None = None) -> dict[str, Any]:
    states = {row["provider"]: row for row in all_provider_states()}
    providers = ("ff_calendar_json", "ff_calendar_scraper", "ff_event_detail", "ff_news")
    items: list[dict[str, Any]] = []
    for provider in providers:
        row = states.get(provider) or {}
        last_sync = row.get("last_successful_sync")
        last_sync_dt = _parse_dt(last_sync)
        state = classify_state(
            last_successful_sync=last_sync_dt,
            schema_changed=bool(row.get("schema_changed")),
            consecutive_failures=int(row.get("consecutive_failures") or 0),
            config=config,
        )
        last_known_good_at = row.get("last_known_good_at")
        last_known_good_dt = _parse_dt(last_known_good_at)
        freshness_seconds = (utcnow() - last_sync_dt).total_seconds() if last_sync_dt else None
        last_known_good_age_seconds = (utcnow() - last_known_good_dt).total_seconds() if last_known_good_dt else None
        next_run_at = (next_due or {}).get(provider)
        items.append(
            {
                "provider": provider,
                "state": state,
                "last_attempt": row.get("last_attempt_at"),
                "last_success": last_sync,
                "last_failure_reason": row.get("last_failure_reason"),
                "records_accepted": row.get("records_accepted") or 0,
                "records_rejected": row.get("records_rejected") or 0,
                "freshness_seconds": freshness_seconds,
                "last_known_good_age_seconds": last_known_good_age_seconds,
                "selector_version": row.get("selector_version"),
                "cache_hits": row.get("cache_hits") or 0,
                "cache_misses": row.get("cache_misses") or 0,
                "retry_count": row.get("retry_count") or 0,
                "consecutive_failures": row.get("consecutive_failures") or 0,
                "next_scheduled_run": next_run_at.isoformat() if next_run_at else None,
                "lock_owner": row.get("lock_owner"),
            }
        )
    return {"items": items, "keys_exposed": False}


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def entry_allowed_for_state(state: str, config: EconomicIntelligenceConfig) -> bool:
    """New-entry fail policy per provider health state. STALE/DEGRADED apply tiered restriction upstream
    (calendar_guard downgrades to REDUCE_SIZE/DELAY); this only governs the UNAVAILABLE fail-closed default."""
    if state == HEALTHY:
        return True
    if state in {STALE, DEGRADED}:
        return True
    return config.ff_provider_fail_mode != "conservative"

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.persistence import all_provider_states, save_provider_state
from backend.shared.cache import cache as shared_cache

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


def record_success(provider: str, *, records_received: int = 0) -> None:
    save_provider_state(provider, status=HEALTHY, last_successful_sync=utcnow(), last_known_good_at=utcnow(), consecutive_failures=0, schema_changed=False, last_failure_reason=None)


def record_failure(provider: str, *, reason: str, schema_changed: bool = False) -> None:
    from backend.economic_intelligence.persistence import provider_state

    existing = provider_state(provider) or {}
    failures = int(existing.get("consecutive_failures") or 0) + 1
    save_provider_state(provider, status=SCHEMA_CHANGED if schema_changed else UNAVAILABLE, consecutive_failures=failures, schema_changed=schema_changed, last_failure_reason=reason)


def health_snapshot(config: EconomicIntelligenceConfig) -> dict[str, Any]:
    states = {row["provider"]: row for row in all_provider_states()}
    providers = ("ff_calendar_json", "ff_calendar_scraper", "ff_event_detail", "ff_news")
    items: list[dict[str, Any]] = []
    for provider in providers:
        row = states.get(provider) or {}
        from datetime import datetime as _dt

        last_sync = row.get("last_successful_sync")
        last_sync_dt = _dt.fromisoformat(last_sync) if isinstance(last_sync, str) else last_sync
        state = classify_state(
            last_successful_sync=last_sync_dt,
            schema_changed=bool(row.get("schema_changed")),
            consecutive_failures=int(row.get("consecutive_failures") or 0),
            config=config,
        )
        items.append({"provider": provider, "state": state, "last_successful_sync": last_sync, "consecutive_failures": row.get("consecutive_failures") or 0, "last_failure_reason": row.get("last_failure_reason")})
    return {"items": items, "keys_exposed": False}


def entry_allowed_for_state(state: str, config: EconomicIntelligenceConfig) -> bool:
    """New-entry fail policy per provider health state. STALE/DEGRADED apply tiered restriction upstream
    (calendar_guard downgrades to REDUCE_SIZE/DELAY); this only governs the UNAVAILABLE fail-closed default."""
    if state == HEALTHY:
        return True
    if state in {STALE, DEGRADED}:
        return True
    return config.ff_provider_fail_mode != "conservative"

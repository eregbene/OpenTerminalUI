from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError, launch_page
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import ScraperSchemaError

logger = logging.getLogger(__name__)


async def fetch_event_detail(detail_url: str, config: EconomicIntelligenceConfig) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch reusable event-definition data from a Forex Factory event detail panel.

    Called only when: the event type is first discovered, its definition is missing/stale,
    or a source-hash change is detected -- never before every trade (see
    persistence.is_definition_stale / service.py's stale-refresh sweep for the call sites).
    """
    if not detail_url:
        return None, {"status": "skipped", "reason": "no_detail_url"}
    started = datetime.now(timezone.utc)
    try:
        async with launch_page(timeout_seconds=config.ff_request_timeout_seconds) as page:
            await page.goto(detail_url, wait_until="domcontentloaded")
            definition = await _extract_definition(page)
    except PlaywrightUnavailableError as exc:
        return None, {"status": "unavailable", "reason": str(exc)}
    except ScraperSchemaError as exc:
        return None, {"status": "schema_changed", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - provider isolation boundary
        return None, {"status": "unavailable", "reason": f"{exc.__class__.__name__}:{exc}"}
    latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return definition, {"status": "ok", "reason": None, "latency_ms": latency_ms}


async def _extract_definition(page: Any) -> dict[str, Any]:
    async def text_of(selector: str) -> str | None:
        el = await page.query_selector(selector)
        if not el:
            return None
        value = (await el.inner_text()).strip()
        return value or None

    event_name = await text_of("h1, .calendarspecs__title, .calendar__event-title")
    if not event_name:
        raise ScraperSchemaError("event_detail_title_not_found")
    definition = {
        "event_name": event_name,
        "source_name": await text_of(".calendarspecs__source, .calendar__source"),
        "source_url": None,
        "why_traders_care": await text_of(".calendarspecs__why, [data-spec='why']"),
        "usual_effect": await text_of(".calendarspecs__usualeffect, [data-spec='usualeffect']"),
        "frequency": await text_of(".calendarspecs__frequency, [data-spec='frequency']"),
        "next_release": await text_of(".calendarspecs__nextrelease, [data-spec='nextrelease']"),
        "derived_via": await text_of(".calendarspecs__derivedvia, [data-spec='derivedvia']"),
        "acronym": await text_of(".calendarspecs__acronym, [data-spec='acronym']"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    definition["source_hash"] = hashlib.sha256(str(sorted(definition.items())).encode("utf-8")).hexdigest()
    return definition

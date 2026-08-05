from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.event_mapping import normalize_event_name
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError, launch_page
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import ScraperSchemaError, month_token

logger = logging.getLogger(__name__)

# v2: the live calendar is a Vue SPA. There is no navigable per-event detail URL -- the
# "Open Detail" trigger (`a.calendar__detail-link`, confirmed live, has no href at all) only
# expands an inline panel via a JS click. Event lookup is therefore by (normalized name,
# approximate scheduled time) against the relevant month's calendar page, not by a stored URL.
EVENT_DETAIL_SELECTOR_VERSION = "v2_click_expand"

_DETAIL_CLICK_TIMEOUT_MS = 6000
_POST_CLICK_WAIT_MS = 1200
_LABELED_FIELDS = {
    "source_name": ("source",),
    "why_traders_care": ("why traders care",),
    "usual_effect": ("usual effect",),
    "frequency": ("frequency",),
    "next_release": ("next release",),
    "derived_via": ("derived via", "how is it derived", "calculation"),
    "acronym": ("acronym", "also known as"),
}


async def fetch_event_detail_by_name(normalized_name: str, near_time: datetime | None, config: EconomicIntelligenceConfig) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch reusable event-definition data by clicking a live calendar row's detail panel.

    Called only when: the event type is first discovered, its definition is missing/stale,
    a source-hash change is detected, or a manual refresh is requested -- never before every
    trade (see persistence.is_definition_stale / service.py's stale-refresh sweep for the
    call sites, and the manual POST /events/{id}/refresh-detail route). A failed or partial
    scrape must not affect trade execution -- event details are enrichment only, and the
    calendar guard works from ff_economic_events/ff_economic_event_snapshots regardless of
    whether a definition exists.
    """
    if not normalized_name:
        return None, {"status": "skipped", "reason": "no_event_name", "selector_version": EVENT_DETAIL_SELECTOR_VERSION}
    started = datetime.now(timezone.utc)
    token = month_token("current" if near_time is None else f"{near_time.year:04d}-{near_time.month:02d}")
    url = f"https://www.forexfactory.com/calendar?month={token}"
    try:
        async with launch_page(timeout_seconds=config.ff_request_timeout_seconds) as page:
            await page.goto(url, wait_until="domcontentloaded")
            definition, status = await _extract_via_click(page, normalized_name, near_time)
    except PlaywrightUnavailableError as exc:
        return None, {"status": "unavailable", "reason": str(exc), "selector_version": EVENT_DETAIL_SELECTOR_VERSION}
    except ScraperSchemaError as exc:
        return None, {"status": "schema_changed", "reason": str(exc), "selector_version": EVENT_DETAIL_SELECTOR_VERSION}
    except Exception as exc:  # noqa: BLE001 - provider isolation boundary
        return None, {"status": "unavailable", "reason": f"{exc.__class__.__name__}:{exc}", "selector_version": EVENT_DETAIL_SELECTOR_VERSION}
    latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    if definition is None:
        return None, {"status": "not_found", "reason": "event_not_found_on_month_calendar", "selector_version": EVENT_DETAIL_SELECTOR_VERSION, "latency_ms": latency_ms}
    return definition, {"status": status, "reason": None if status == "ok" else "detail_panel_fields_incomplete", "latency_ms": latency_ms, "selector_version": EVENT_DETAIL_SELECTOR_VERSION}


async def _extract_via_click(page: Any, normalized_name: str, near_time: datetime | None) -> tuple[dict[str, Any] | None, str]:
    rows = await page.query_selector_all("tr[data-event-id]")
    if not rows:
        raise ScraperSchemaError("no_calendar_rows_found_for_event_detail_lookup")
    best_row = None
    for row in rows:
        title_el = await row.query_selector("td[class*='calendar__event'] .calendar__event-title, td[class*='calendar__event']")
        if not title_el:
            continue
        title_text = normalize_event_name((await title_el.inner_text()).strip())
        if title_text == normalized_name:
            best_row = row
            break
        if best_row is None and normalized_name in title_text:
            best_row = row
    if best_row is None:
        return None, "not_found"

    event_name_el = await best_row.query_selector("td[class*='calendar__event']")
    event_name = (await event_name_el.inner_text()).strip() if event_name_el else normalized_name
    definition: dict[str, Any] = {"event_name": event_name, "source_name": None, "source_url": None, "why_traders_care": None, "usual_effect": None, "frequency": None, "next_release": None, "derived_via": None, "acronym": None, "fetched_at": datetime.now(timezone.utc).isoformat()}

    trigger = await best_row.query_selector("a.calendar__detail-link, td[class*='calendar__detail'] a")
    if trigger is None:
        definition["source_hash"] = _hash(definition)
        return definition, "partial"

    try:
        await trigger.click(force=True, no_wait_after=True, timeout=_DETAIL_CLICK_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001 - a failed click degrades to a partial definition, never a crash
        logger.debug("Forex Factory event-detail click failed, returning row-level fields only: %s", exc.__class__.__name__)
        definition["source_hash"] = _hash(definition)
        return definition, "partial"

    await page.wait_for_timeout(_POST_CLICK_WAIT_MS)
    fields_found = await _extract_labeled_fields(page)
    definition.update({key: value for key, value in fields_found.items() if value})
    definition["source_hash"] = _hash(definition)
    status = "ok" if any(fields_found.values()) else "partial"
    return definition, status


async def _extract_labeled_fields(page: Any) -> dict[str, str | None]:
    """Best-effort field extraction from the expanded detail panel. The panel's exact CSS
    class was not fully pinned against the live DOM within the research budget for this
    change, so extraction here is deliberately label-text-anchored (looking for the field
    labels Forex Factory shows, e.g. "Source", "Why Traders Care") rather than a single exact
    class selector -- this is the same "avoid depending on one exact class string" approach
    already used for the calendar/news scrapers, and it fails safe: if no labels are found,
    the caller treats the definition as 'partial' (row-level fields only), never malformed."""
    results: dict[str, str | None] = dict.fromkeys(_LABELED_FIELDS, None)
    try:
        body_text = await page.inner_text("body")
    except Exception:
        return results
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        lowered = line.lower().rstrip(":")
        for field, labels in _LABELED_FIELDS.items():
            if results[field] is not None:
                continue
            if lowered in labels and index + 1 < len(lines):
                candidate = lines[index + 1].strip()
                if candidate and candidate.lower() not in labels:
                    results[field] = candidate[:2000]
    return results


def _hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(str(sorted((k, v) for k, v in definition.items() if k != "fetched_at")).encode("utf-8")).hexdigest()

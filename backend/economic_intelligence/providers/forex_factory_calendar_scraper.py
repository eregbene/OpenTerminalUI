from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError, launch_page

logger = logging.getLogger(__name__)

_MONTH_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


class ScraperSchemaError(Exception):
    """Raised when the Forex Factory calendar page's structure no longer matches expected
    selectors -- this marks the run SCHEMA_CHANGED and stops cleanly rather than emitting
    partial/malformed trading data. A change to Forex Factory HTML must produce this, not
    a crash and not silently wrong rows."""


def month_token(value: str) -> str:
    """Accepts 'YYYY-MM', 'current', or 'next' and returns Forex Factory's 'mon.YYYY' token."""
    now = datetime.now(timezone.utc)
    if value == "current":
        return f"{_MONTH_ABBR[now.month - 1]}.{now.year}"
    if value == "next":
        month = now.month + 1
        year = now.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        return f"{_MONTH_ABBR[month - 1]}.{year}"
    year_str, month_str = value.split("-")
    return f"{_MONTH_ABBR[int(month_str) - 1]}.{int(year_str)}"


async def scrape_month(month: str, config: EconomicIntelligenceConfig, *, debug_raw_html: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape one month of the Forex Factory calendar (fields/history not reliably available
    from the weekly JSON feed). Never raises out of this function -- returns (rows, meta)."""
    started = datetime.now(timezone.utc)
    token = month_token(month)
    year = int(token.split(".")[1])
    url = f"https://www.forexfactory.com/calendar?month={token}"
    try:
        async with launch_page(timeout_seconds=config.ff_request_timeout_seconds) as page:
            await page.goto(url, wait_until="domcontentloaded")
            rows = await _extract_rows(page, debug_raw_html=debug_raw_html, year=year)
    except PlaywrightUnavailableError as exc:
        return [], {"status": "unavailable", "reason": str(exc), "month": token}
    except ScraperSchemaError as exc:
        logger.warning("Forex Factory calendar scraper schema change for %s: %s", token, exc)
        return [], {"status": "schema_changed", "reason": str(exc), "month": token}
    except Exception as exc:  # noqa: BLE001 - provider isolation boundary
        return [], {"status": "unavailable", "reason": f"{exc.__class__.__name__}:{exc}", "month": token}
    latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return rows, {"status": "ok", "reason": None, "month": token, "latency_ms": latency_ms, "records_received": len(rows)}


async def _extract_rows(page: Any, *, debug_raw_html: bool, year: int | None = None) -> list[dict[str, Any]]:
    """Row parsing uses semantic/structural selectors (not brittle exact class-name equality).

    Forex Factory's calendar table historically uses `tr.calendar__row` with per-cell
    `calendar__<field>` classes; we match on substring/semantic selectors so minor class
    renames don't break parsing, while a wholesale structural change (no rows found at all
    on a non-empty page) raises ScraperSchemaError.
    """
    row_handles = await page.query_selector_all("tr[class*='calendar__row'], tr[data-event-id]")
    if not row_handles:
        body_text = await page.inner_text("body")
        if len(body_text.strip()) < 200:
            raise ScraperSchemaError("calendar_page_body_empty_or_blocked")
        raise ScraperSchemaError("no_calendar_rows_found")
    rows: list[dict[str, Any]] = []
    current_date: str | None = None
    for handle in row_handles:
        try:
            date_cell = await handle.query_selector("td[class*='calendar__date']")
            if date_cell:
                text = (await date_cell.inner_text()).strip()
                if text:
                    current_date = text
            currency_cell = await handle.query_selector("td[class*='calendar__currency']")
            impact_cell = await handle.query_selector("td[class*='calendar__impact'] span")
            event_cell = await handle.query_selector("td[class*='calendar__event']")
            time_cell = await handle.query_selector("td[class*='calendar__time']")
            actual_cell = await handle.query_selector("td[class*='calendar__actual']")
            forecast_cell = await handle.query_selector("td[class*='calendar__forecast']")
            previous_cell = await handle.query_selector("td[class*='calendar__previous']")
            if not currency_cell or not event_cell:
                continue
            currency = (await currency_cell.inner_text()).strip().upper()
            event_name = (await event_cell.inner_text()).strip()
            if not currency or not event_name:
                continue
            impact = (await impact_cell.get_attribute("title") if impact_cell else None) or ""
            event_id = await handle.get_attribute("data-event-id")
            row_time = (await time_cell.inner_text()).strip() if time_cell else ""
            raw_scheduled_at = f"{current_date} {row_time}".strip() or None
            raw_html = await handle.inner_html() if debug_raw_html else None
            rows.append(
                {
                    "provider_event_id": event_id or hashlib.sha256(f"{currency}|{event_name}|{current_date}|{row_time}".encode()).hexdigest()[:32],
                    "raw_name": event_name,
                    "currency": currency,
                    "impact": _impact_from_title(impact),
                    "scheduled_at": _scheduled_at_iso(raw_scheduled_at, year),
                    "raw_scheduled_at": raw_scheduled_at,
                    "source_timezone": "forex_factory_scraper",
                    "actual_raw": (await actual_cell.inner_text()).strip() if actual_cell else None,
                    "forecast_raw": (await forecast_cell.inner_text()).strip() if forecast_cell else None,
                    "previous_raw": (await previous_cell.inner_text()).strip() if previous_cell else None,
                    "detail_url": await _detail_href(handle),
                    "has_detail_action": bool(await handle.query_selector("td[class*='calendar__graph'], a[class*='calendar__details']")),
                    "raw_row_html": raw_html,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the whole month
            logger.debug("Skipping unparseable Forex Factory calendar row: %s", exc)
            continue
    return rows


async def _detail_href(handle: Any) -> str | None:
    link = await handle.query_selector("a[href*='calendar']")
    return await link.get_attribute("href") if link else None


def _impact_from_title(title: str) -> str:
    text = title.lower()
    if "high" in text:
        return "high"
    if "medium" in text or "moderate" in text:
        return "medium"
    if "low" in text:
        return "low"
    if "holiday" in text:
        return "none"
    return "unknown"


def _scheduled_at_iso(raw: str | None, year: int | None) -> str | None:
    if not raw or year is None:
        return raw
    text = " ".join(str(raw).replace("\n", " ").split())
    parts = text.split()
    if len(parts) < 3:
        return raw
    month = parts[1][:3].lower()
    if month not in _MONTH_ABBR:
        return raw
    try:
        day = int(parts[2])
    except ValueError:
        return raw
    hour = 0
    minute = 0
    if len(parts) >= 4:
        token = parts[3].lower()
        if token in {"all", "day", "tentative"} or token.startswith("day"):
            token = ""
        if token:
            try:
                parsed_time = datetime.strptime(token, "%I:%M%p").time()
                hour = parsed_time.hour
                minute = parsed_time.minute
            except ValueError:
                try:
                    parsed_time = datetime.strptime(token, "%I%p").time()
                    hour = parsed_time.hour
                    minute = parsed_time.minute
                except ValueError:
                    pass
    return datetime(year, _MONTH_ABBR.index(month) + 1, day, hour, minute, tzinfo=timezone.utc).isoformat()


async def backfill_range(start_month: str, end_month: str, config: EconomicIntelligenceConfig, *, resume_from: str | None = None) -> dict[str, Any]:
    """Controlled start->end backfill with resume checkpoint and per-run month cap."""
    months = _month_sequence(start_month, end_month)
    if resume_from and resume_from in months:
        months = months[months.index(resume_from):]
    months = months[: max(1, config.ff_backfill_max_months_per_run)]
    results: dict[str, Any] = {}
    for index, month in enumerate(months):
        results[month] = await scrape_month(month, config)
        if index < len(months) - 1:
            await asyncio.sleep(max(0, config.ff_backfill_delay_seconds))
    next_checkpoint = months[-1] if months else None
    return {"status": "ok", "months_processed": months, "next_checkpoint": next_checkpoint, "results": results}


def _month_sequence(start_month: str, end_month: str) -> list[str]:
    start_year, start_mon = (int(part) for part in start_month.split("-"))
    end_year, end_mon = (int(part) for part in end_month.split("-"))
    months: list[str] = []
    year, mon = start_year, start_mon
    while (year, mon) <= (end_year, end_mon):
        months.append(f"{year:04d}-{mon:02d}")
        mon += 1
        if mon > 12:
            mon = 1
            year += 1
    return months

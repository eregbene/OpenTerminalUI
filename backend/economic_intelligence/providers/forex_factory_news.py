from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.event_mapping import SUPPORTED_CURRENCIES
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError, launch_page
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import ScraperSchemaError

logger = logging.getLogger(__name__)

_NEWS_URL = "https://www.forexfactory.com/news"


async def fetch_news(config: EconomicIntelligenceConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape Forex Factory news list metadata only -- never stores or republishes full
    article bodies. Never raises out of this function."""
    started = datetime.now(timezone.utc)
    try:
        async with launch_page(timeout_seconds=config.ff_request_timeout_seconds) as page:
            await page.goto(_NEWS_URL, wait_until="domcontentloaded")
            rows = await _extract_news(page)
    except PlaywrightUnavailableError as exc:
        return [], {"status": "unavailable", "reason": str(exc)}
    except ScraperSchemaError as exc:
        return [], {"status": "schema_changed", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - provider isolation boundary
        return [], {"status": "unavailable", "reason": f"{exc.__class__.__name__}:{exc}"}
    latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return rows, {"status": "ok", "reason": None, "latency_ms": latency_ms, "records_received": len(rows)}


async def _extract_news(page: Any) -> list[dict[str, Any]]:
    item_handles = await page.query_selector_all("div[class*='news__item'], article[class*='news']")
    if not item_handles:
        body_text = await page.inner_text("body")
        if len(body_text.strip()) < 200:
            raise ScraperSchemaError("news_page_body_empty_or_blocked")
        raise ScraperSchemaError("no_news_items_found")
    rows: list[dict[str, Any]] = []
    for handle in item_handles:
        try:
            headline_el = await handle.query_selector("a[class*='news__title'], h2 a, h3 a")
            if not headline_el:
                continue
            headline = (await headline_el.inner_text()).strip()
            href = await headline_el.get_attribute("href")
            if not headline or not href:
                continue
            time_el = await handle.query_selector("[class*='news__time'], time")
            source_el = await handle.query_selector("[class*='news__source']")
            preview_el = await handle.query_selector("[class*='news__excerpt'], p")
            category_el = await handle.query_selector("[class*='news__category']")
            published_raw = (await time_el.get_attribute("datetime") if time_el else None) or (await time_el.inner_text() if time_el else None)
            preview = (await preview_el.inner_text()).strip()[:400] if preview_el else None
            related = _related_currencies(headline, preview or "")
            rows.append(
                {
                    "provider_story_id": _story_id(href),
                    "headline": headline,
                    "forex_factory_url": href if href.startswith("http") else f"https://www.forexfactory.com{href}",
                    "published_at_utc": published_raw,
                    "source_name": (await source_el.inner_text()).strip() if source_el else None,
                    "source_url": None,
                    "category": (await category_el.inner_text()).strip() if category_el else None,
                    "related_currencies": sorted(related),
                    "provider_impact": None,
                    "preview": preview,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole page
            logger.debug("Skipping unparseable Forex Factory news item: %s", exc)
            continue
    return rows


def _story_id(href: str) -> str:
    return hashlib.sha256(href.encode("utf-8")).hexdigest()[:32]


def _related_currencies(headline: str, preview: str) -> set[str]:
    text = f"{headline} {preview}".upper()
    return {currency for currency in SUPPORTED_CURRENCIES if currency in text}

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.event_mapping import SUPPORTED_CURRENCIES
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError, launch_page
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import ScraperSchemaError

logger = logging.getLogger(__name__)

_NEWS_URL = "https://www.forexfactory.com/news"
_BASE_URL = "https://www.forexfactory.com"

# v2: rewritten against the live DOM (2026-08) after v1 (news__item/article/flexposts class
# guesses) matched zero elements. The live page is a Vue SPA; the one stable, layout-independent
# signal is the story URL *shape* itself -- every headline, on every layout the page uses
# (hot-story widget, plain list), is an <a href="/news/{numeric_id}-{slug}">. Matching on that
# shape rather than a component class name is what makes this resilient across layouts.
NEWS_SELECTOR_VERSION = "v2_href_shape"

_STORY_HREF_RE = re.compile(r"^/news/(\d+)-[a-z0-9-]+/?$", re.IGNORECASE)
_RELATIVE_TIME_RE = re.compile(r"(\d+)\s*(min|mins|minute|minutes|hr|hrs|hour|hours|day|days)\s*ago", re.IGNORECASE)
_SOURCE_TEXT_RE = re.compile(r"from\s+@?(.+)", re.IGNORECASE)
_UNIT_SECONDS = {"min": 60, "hr": 3600, "hour": 3600, "day": 86400}


async def fetch_news(config: EconomicIntelligenceConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape Forex Factory news list metadata only -- never stores or republishes full
    article bodies. Never raises out of this function."""
    started = datetime.now(timezone.utc)
    try:
        async with launch_page(timeout_seconds=config.ff_request_timeout_seconds) as page:
            await page.goto(_NEWS_URL, wait_until="domcontentloaded")
            rows = await _extract_news(page)
    except PlaywrightUnavailableError as exc:
        return [], {"status": "unavailable", "reason": str(exc), "selector_version": NEWS_SELECTOR_VERSION}
    except ScraperSchemaError as exc:
        logger.warning("Forex Factory news selector sanity failure: %s (selector_version=%s)", exc, NEWS_SELECTOR_VERSION)
        return [], {"status": "schema_changed", "reason": str(exc), "selector_version": NEWS_SELECTOR_VERSION}
    except Exception as exc:  # noqa: BLE001 - provider isolation boundary
        return [], {"status": "unavailable", "reason": f"{exc.__class__.__name__}:{exc}", "selector_version": NEWS_SELECTOR_VERSION}
    latency_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    sane, reason, stats = _sanity_check(rows, config)
    if not sane:
        logger.warning("Forex Factory news sanity check failed: %s stats=%s (selector_version=%s)", reason, stats, NEWS_SELECTOR_VERSION)
        return [], {"status": "schema_changed", "reason": reason, "selector_version": NEWS_SELECTOR_VERSION, "stats": stats}
    status = "ok" if stats["time_parse_ratio"] >= 0.5 and stats["count"] >= config.ff_news_min_expected_items else "degraded"
    return rows, {
        "status": status,
        "reason": None if status == "ok" else "low_time_parse_ratio_or_below_full_expected_count",
        "latency_ms": latency_ms,
        "records_received": len(rows),
        "selector_version": NEWS_SELECTOR_VERSION,
        "stats": stats,
    }


def _sanity_check(rows: list[dict[str, Any]], config: EconomicIntelligenceConfig) -> tuple[bool, str | None, dict[str, Any]]:
    """The 6 sanity checks: min/max plausible count, non-empty headline ratio, valid URL
    ratio, duplicate ratio, publication-time parse ratio. A hard failure on count/headline/
    URL/duplicate marks the run SCHEMA_CHANGED (no malformed data persisted); a low
    time-parse ratio alone only downgrades the run to DEGRADED (still usable)."""
    count = len(rows)
    if count == 0:
        return False, "zero_stories_extracted", {"count": 0}
    non_empty_headline_ratio = sum(1 for r in rows if str(r.get("headline") or "").strip()) / count
    valid_url_ratio = sum(1 for r in rows if _is_valid_ff_url(r.get("forex_factory_url"))) / count
    story_ids = [r.get("provider_story_id") for r in rows]
    duplicate_ratio = 1 - (len(set(story_ids)) / count) if count else 0.0
    time_parse_ratio = sum(1 for r in rows if r.get("published_at_utc")) / count
    stats = {
        "count": count,
        "non_empty_headline_ratio": non_empty_headline_ratio,
        "valid_url_ratio": valid_url_ratio,
        "duplicate_ratio": duplicate_ratio,
        "time_parse_ratio": time_parse_ratio,
    }
    if count < config.ff_news_min_expected_items:
        return False, f"story_count_below_minimum:{count}<{config.ff_news_min_expected_items}", stats
    if count > config.ff_news_max_expected_items:
        return False, f"story_count_above_maximum:{count}>{config.ff_news_max_expected_items}", stats
    if non_empty_headline_ratio < 0.95:
        return False, f"headline_ratio_too_low:{non_empty_headline_ratio:.2f}", stats
    if valid_url_ratio < config.ff_news_min_valid_url_ratio:
        return False, f"valid_url_ratio_too_low:{valid_url_ratio:.2f}", stats
    if duplicate_ratio > config.ff_news_max_duplicate_ratio:
        return False, f"duplicate_ratio_too_high:{duplicate_ratio:.2f}", stats
    return True, None, stats


def _is_valid_ff_url(url: Any) -> bool:
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.netloc.endswith("forexfactory.com")


async def _extract_news(page: Any) -> list[dict[str, Any]]:
    """Every real story on the page is an <a href="/news/{id}-{slug}">, regardless of which
    widget/layout it appears in (hot-story cards, plain list) -- matching on that URL shape
    is layout-independent. The same story's title link can appear more than once in the DOM
    (responsive breakpoint duplicates), so results are deduped by the numeric story ID here,
    at extraction time, not left to downstream content-hash dedup alone."""
    anchors = await page.query_selector_all("a[href*='/news/']")
    if not anchors:
        body_text = await page.inner_text("body")
        if len(body_text.strip()) < 200:
            raise ScraperSchemaError("news_page_body_empty_or_blocked")
        raise ScraperSchemaError("no_news_story_links_found")

    seen: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for anchor in anchors:
        try:
            href = await anchor.get_attribute("href")
            if not href:
                continue
            path = href if href.startswith("/") else href.replace(_BASE_URL, "", 1)
            match = _STORY_HREF_RE.match(path.split("?")[0].split("#")[0])
            if not match:
                continue
            story_id = match.group(1)
            if story_id in seen:
                continue
            headline = ((await anchor.inner_text()) or (await anchor.get_attribute("title")) or "").strip()
            if not headline:
                continue
            forex_factory_url = f"{_BASE_URL}{path}" if path.startswith("/") else path
            container = await anchor.evaluate_handle("el => el.closest('article, li, div') || el.parentElement")
            preview_text = None
            source_name = None
            source_url = None
            published_raw = None
            impact = None
            try:
                container_el = container.as_element()
                if container_el:
                    time_el = await container_el.query_selector("[class*='nowrap'], time")
                    if time_el:
                        published_raw = (await time_el.inner_text()).strip()
                    hit_link = await container_el.query_selector(f"a[href*='/news/{story_id}-'][href$='/hit'], a[href*='/hit']")
                    if hit_link:
                        hit_text = (await hit_link.inner_text()).strip()
                        source_match = _SOURCE_TEXT_RE.match(hit_text)
                        source_name = source_match.group(1).strip() if source_match else (hit_text or None)
                        source_url = await hit_link.get_attribute("href")
                        if source_url and source_url.startswith("/"):
                            source_url = f"{_BASE_URL}{source_url}"
                    impact_el = await container_el.query_selector("img[class*='impact']")
                    if impact_el:
                        impact_class = (await impact_el.get_attribute("class")) or ""
                        impact = _impact_from_class(impact_class)
                    preview_el = await container_el.query_selector("p, [class*='excerpt'], [class*='summary']")
                    if preview_el:
                        preview_candidate = (await preview_el.inner_text()).strip()
                        if preview_candidate and preview_candidate != headline:
                            preview_text = preview_candidate[:400]
            except Exception:  # noqa: BLE001 - enrichment only; headline/url already captured
                pass
            related = _related_currencies(headline, preview_text or "")
            published_at = _parse_relative_time(published_raw, now)
            seen[story_id] = {
                "provider_story_id": story_id,
                "headline": headline,
                "forex_factory_url": forex_factory_url,
                "published_at_utc": published_at.isoformat() if published_at else None,
                "source_name": source_name,
                "source_url": source_url,
                "category": None,
                "related_currencies": sorted(related),
                "provider_impact": impact,
                "preview": preview_text,
                "scraped_at": now.isoformat(),
                "selector_version": NEWS_SELECTOR_VERSION,
            }
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole page
            logger.debug("Skipping unparseable Forex Factory news item: %s", exc)
            continue
    return list(seen.values())


def _parse_relative_time(text: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    match = _RELATIVE_TIME_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower().rstrip("s")
    seconds = _UNIT_SECONDS.get(unit)
    if not seconds:
        return None
    return now - timedelta(seconds=amount * seconds)


def _impact_from_class(class_attr: str) -> str | None:
    text = class_attr.lower()
    if "high" in text:
        return "high"
    if "medium" in text or "orange" in text:
        return "medium"
    if "low" in text or "yellow" in text or "yel" in text:
        return "low"
    return None


def _related_currencies(headline: str, preview: str) -> set[str]:
    text = f"{headline} {preview}".upper()
    return {currency for currency in SUPPORTED_CURRENCIES if currency in text}

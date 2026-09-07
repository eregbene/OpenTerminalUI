from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class PlaywrightUnavailableError(Exception):
    """Raised when the playwright package or its browser binaries are not installed."""


@asynccontextmanager
async def launch_page(*, timeout_seconds: int) -> AsyncIterator[Any]:
    """Shared headless Chromium page launcher for Forex Factory scraper providers.

    One browser context per call (never per-trade) -- callers are background ingestion
    jobs only, never the trading cycle. Raises PlaywrightUnavailableError (not raw
    ImportError) when Playwright isn't installed so callers can degrade cleanly.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise PlaywrightUnavailableError("playwright package not installed") from exc
    async with async_playwright() as pw:
        timeout_ms = timeout_seconds * 1000
        browser = await asyncio.wait_for(pw.chromium.launch(headless=True, timeout=timeout_ms), timeout=timeout_seconds + 5)
        try:
            context = await asyncio.wait_for(
                browser.new_context(user_agent="BensimTrading-EconomicIntelligence/1.0 (+demo-mt5-research)"),
                timeout=timeout_seconds,
            )
            page = await asyncio.wait_for(context.new_page(), timeout=timeout_seconds)
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            yield page
        finally:
            await browser.close()

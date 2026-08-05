from __future__ import annotations

import asyncio
import json

import httpx

from backend.economic_intelligence.config import EconomicIntelligenceConfig, economic_intelligence_config
from backend.economic_intelligence.providers import forex_factory_calendar as ff_calendar
from backend.economic_intelligence.providers import forex_factory_calendar_scraper as ff_scraper

CFG = economic_intelligence_config()


class _FakeResponse:
    def __init__(self, *, status_code=200, json_payload=None, content_type="application/json", text=""):
        self.status_code = status_code
        self._json_payload = json_payload
        self.headers = {"content-type": content_type}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_payload


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        if self._exc:
            raise self._exc
        return self._response


def test_fetch_weekly_calendar_normalizes_valid_rows(monkeypatch):
    payload = [{"id": "1", "title": "Non-Farm Payrolls", "country": "USD", "date": "2026-08-07T12:30:00Z", "impact": "High", "forecast": "185K", "previous": "150K", "actual": ""}]
    fake_client = _FakeClient(response=_FakeResponse(json_payload=payload, text=json.dumps(payload)))
    monkeypatch.setattr(ff_calendar.httpx, "AsyncClient", lambda **kwargs: fake_client)
    rows, meta = asyncio.run(ff_calendar.fetch_weekly_calendar(CFG))
    assert meta["status"] == "ok"
    assert len(rows) == 1
    assert rows[0]["currency"] == "USD"
    assert rows[0]["impact"] == "high"


def test_fetch_weekly_calendar_timeout_does_not_raise(monkeypatch):
    fake_client = _FakeClient(exc=httpx.TimeoutException("boom"))
    monkeypatch.setattr(ff_calendar.httpx, "AsyncClient", lambda **kwargs: fake_client)
    fast_config = EconomicIntelligenceConfig(ff_max_retries=1)
    rows, meta = asyncio.run(ff_calendar.fetch_weekly_calendar(fast_config))
    assert rows == []
    assert meta["status"] in {"timeout", "unavailable"}


def test_fetch_weekly_calendar_schema_change_not_a_crash(monkeypatch):
    fake_client = _FakeClient(response=_FakeResponse(json_payload={"not": "a list"}, text='{"not": "a list"}'))
    monkeypatch.setattr(ff_calendar.httpx, "AsyncClient", lambda **kwargs: fake_client)
    rows, meta = asyncio.run(ff_calendar.fetch_weekly_calendar(CFG))
    assert rows == []
    assert meta["status"] == "schema_changed"


def test_fetch_weekly_calendar_missing_required_fields_are_skipped_not_crashed(monkeypatch):
    payload = [{"title": "Missing country field"}, {"id": "2", "title": "GDP", "country": "EUR", "date": "2026-08-07T10:00:00Z", "impact": "medium"}]
    fake_client = _FakeClient(response=_FakeResponse(json_payload=payload, text=json.dumps(payload)))
    monkeypatch.setattr(ff_calendar.httpx, "AsyncClient", lambda **kwargs: fake_client)
    rows, meta = asyncio.run(ff_calendar.fetch_weekly_calendar(CFG))
    assert meta["status"] == "ok"
    assert len(rows) == 1
    assert rows[0]["currency"] == "EUR"


def test_month_token_accepts_shorthand_and_explicit():
    assert ff_scraper.month_token("2025-01") == "jan.2025"
    assert ff_scraper.month_token("2025-12") == "dec.2025"
    assert ff_scraper.month_token("current")
    assert ff_scraper.month_token("next")


def test_scraper_never_raises_regardless_of_playwright_availability():
    """A Forex Factory failure must not crash the trading cycle -- this holds whether
    Playwright's browser is installed (real scrape, status 'ok') or not ('unavailable').
    Either way the call must never raise, and rows must be empty unless status is 'ok'."""
    rows, meta = asyncio.run(ff_scraper.scrape_month("2026-08", CFG))
    assert meta["status"] in {"unavailable", "ok", "schema_changed"}
    if meta["status"] != "ok":
        assert rows == []
    else:
        assert isinstance(rows, list)

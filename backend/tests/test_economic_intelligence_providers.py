from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.economic_intelligence.config import EconomicIntelligenceConfig, economic_intelligence_config
from backend.economic_intelligence.providers import forex_factory_calendar as ff_calendar
from backend.economic_intelligence.providers import forex_factory_calendar_scraper as ff_scraper
from backend.economic_intelligence.providers import forex_factory_event_detail as ff_detail
from backend.economic_intelligence.providers import forex_factory_news as ff_news
from backend.economic_intelligence.providers._browser import PlaywrightUnavailableError

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


# --- fake Playwright primitives for news/event-detail extraction tests -----------------


class _FakeQuery:
    """A queryable node: holds sub-selectors keyed by a substring match against the
    selector string passed to query_selector/query_selector_all."""

    def __init__(self, *, text="", attrs=None, children=None):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or {}

    async def inner_text(self):
        return self._text

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def query_selector(self, selector):
        for key, node in self._children.items():
            if key in selector:
                return node
        return None

    async def query_selector_all(self, selector):
        return [node for key, node in self._children.items() if key in selector]

    async def evaluate_handle(self, _script):
        return _FakeHandle(self)

    async def evaluate(self, _script):
        return self._attrs.get("__html__", "")

    async def click(self, **kwargs):
        self._attrs["__clicked__"] = True


class _FakeHandle:
    def __init__(self, element):
        self._element = element

    def as_element(self):
        return self._element


class _FakeNewsPage:
    def __init__(self, anchors, body_text="x" * 300):
        self._anchors = anchors
        self._body_text = body_text

    async def goto(self, *args, **kwargs):
        return None

    async def query_selector_all(self, selector):
        if "/news/" in selector:
            return self._anchors
        return []

    async def inner_text(self, selector=None):
        return self._body_text


def _news_anchor(href, text, *, time_text=None, hit_text=None, impact_class=None):
    children = {}
    if time_text is not None:
        children["nowrap"] = _FakeQuery(text=time_text)
    if hit_text is not None:
        children["hit"] = _FakeQuery(text=hit_text, attrs={"href": f"{href}/hit"})
    if impact_class is not None:
        children["impact"] = _FakeQuery(attrs={"class": impact_class})
    container = _FakeQuery(children=children)
    anchor = _FakeQuery(text=text, attrs={"href": href})
    anchor._container = container  # noqa: SLF001 - test fixture wiring

    async def evaluate_handle(_script):
        return _FakeHandle(container)

    anchor.evaluate_handle = evaluate_handle
    return anchor


@pytest.fixture()
def patched_launch_page(monkeypatch):
    def _patch(module, page):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_launch_page(**kwargs):
            yield page

        monkeypatch.setattr(module, "launch_page", _fake_launch_page)

    return _patch


# --- news scraper: tests 1-9 ------------------------------------------------------------


def test_news_extraction_parses_valid_stories(patched_launch_page):
    relaxed_cfg = EconomicIntelligenceConfig(ff_news_min_expected_items=1)
    anchors = [
        _news_anchor("/news/1000001-fed-hints-at-cut", "Fed hints at rate cut", time_text="2 hr ago", hit_text="From @RedboxWire", impact_class="svg-img--impact-ff-low"),
        _news_anchor("/news/1000002-ecb-holds-rates", "ECB holds rates steady", time_text="10 min ago", hit_text="From @financialjuice", impact_class="svg-img--impact-ff-medium"),
    ]
    patched_launch_page(ff_news, _FakeNewsPage(anchors))
    rows, meta = asyncio.run(ff_news.fetch_news(relaxed_cfg))
    assert meta["status"] in {"ok", "degraded"}
    assert len(rows) == 2
    assert {r["provider_story_id"] for r in rows} == {"1000001", "1000002"}
    assert rows[0]["source_name"] in {"RedboxWire", "financialjuice"}
    assert all(r["published_at_utc"] for r in rows)
    assert {r["provider_impact"] for r in rows} == {"low", "medium"}


def test_news_extraction_excludes_navigation_links(patched_launch_page):
    relaxed_cfg = EconomicIntelligenceConfig(ff_news_min_expected_items=1)
    anchors = [
        _news_anchor("/news/1000001-real-story", "Real Story", time_text="1 hr ago"),
        _news_anchor("/news/category/forex", "Forex category page"),
        _news_anchor("/news", "News home"),
        _news_anchor("/news/1000001-real-story/hit", "From @Source"),
        _news_anchor("/news/1000001-real-story#comments", "12 comments"),
    ]
    patched_launch_page(ff_news, _FakeNewsPage(anchors))
    rows, _meta = asyncio.run(ff_news.fetch_news(relaxed_cfg))
    assert len(rows) == 1
    assert rows[0]["provider_story_id"] == "1000001"


def test_news_extraction_collapses_responsive_duplicates(patched_launch_page):
    relaxed_cfg = EconomicIntelligenceConfig(ff_news_min_expected_items=1)
    anchors = [
        _news_anchor("/news/1000001-same-story", "Same Story", time_text="5 min ago"),
        _news_anchor("/news/1000001-same-story", "Same Story", time_text="5 min ago"),
    ]
    patched_launch_page(ff_news, _FakeNewsPage(anchors))
    rows, _meta = asyncio.run(ff_news.fetch_news(relaxed_cfg))
    assert len(rows) == 1


def test_news_extraction_missing_time_degrades_safely(patched_launch_page):
    relaxed_cfg = EconomicIntelligenceConfig(ff_news_min_expected_items=1)
    anchors = [_news_anchor("/news/1000001-no-time", "No Time Story")]
    patched_launch_page(ff_news, _FakeNewsPage(anchors))
    rows, meta = asyncio.run(ff_news.fetch_news(relaxed_cfg))
    assert meta["status"] in {"ok", "degraded", "schema_changed"}
    if rows:
        assert rows[0]["published_at_utc"] is None


def test_news_sanity_check_low_valid_url_ratio_triggers_schema_changed():
    rows = [{"headline": f"Story {i}", "forex_factory_url": None if i < 4 else "https://www.forexfactory.com/news/1-a", "provider_story_id": str(i), "published_at_utc": "2026-01-01T00:00:00+00:00"} for i in range(6)]
    ok, reason, stats = ff_news._sanity_check(rows, CFG)
    assert not ok
    assert "valid_url_ratio" in reason
    assert stats["count"] == 6


def test_news_sanity_check_excess_duplicate_ratio_triggers_schema_changed():
    rows = [{"headline": f"Story {i}", "forex_factory_url": "https://www.forexfactory.com/news/1-a", "provider_story_id": "1", "published_at_utc": "2026-01-01T00:00:00+00:00"} for i in range(6)]
    ok, reason, _stats = ff_news._sanity_check(rows, CFG)
    assert not ok
    assert "duplicate_ratio" in reason


def test_news_sanity_check_partial_valid_result_is_degraded_not_failed():
    rows = [{"headline": f"Story {i}", "forex_factory_url": "https://www.forexfactory.com/news/1-a", "provider_story_id": str(i), "published_at_utc": None} for i in range(6)]
    ok, _reason, stats = ff_news._sanity_check(rows, CFG)
    assert ok  # sanity passes (count/url/headline/duplicate all fine)
    assert stats["time_parse_ratio"] == 0.0  # but low time-parse ratio -> caller marks DEGRADED, not SCHEMA_CHANGED


def test_news_refresh_schema_changed_does_not_touch_last_known_good(monkeypatch):
    from backend.economic_intelligence import provider_health, service

    called = {"cached": False}

    async def _fake_cache(*args, **kwargs):
        called["cached"] = True

    monkeypatch.setattr(provider_health, "cache_last_known_good", _fake_cache)
    monkeypatch.setattr(service, "fetch_news", lambda config: asyncio.sleep(0, result=([], {"status": "schema_changed", "reason": "no_news_story_links_found", "selector_version": ff_news.NEWS_SELECTOR_VERSION})))
    monkeypatch.setattr(service.provider_health, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(service, "upsert_news_item", lambda row: {"created": False})
    result = asyncio.run(service.economic_intelligence_service._refresh_news())
    assert result["status"] == "schema_changed"
    assert called["cached"] is False  # last-known-good is preserved, never overwritten by a failed run


def test_provider_recovers_from_schema_changed_to_healthy(monkeypatch):
    from backend.economic_intelligence import provider_health

    monkeypatch.setattr(provider_health, "provider_state", lambda name: {"schema_changed": True})
    monkeypatch.setattr(provider_health, "save_provider_state", lambda *a, **k: None)
    logged = {"warned": False}
    monkeypatch.setattr(provider_health.logger, "warning", lambda *a, **k: logged.__setitem__("warned", True))
    provider_health.record_success("ff_news", records_received=10)
    assert logged["warned"] is True


# --- event detail: tests 10-15 -----------------------------------------------------------


def _detail_row(event_id, title, *, has_trigger=True):
    trigger_children = {}
    if has_trigger:
        trigger_children["calendar__detail-link"] = _FakeQuery()
    row = _FakeQuery(attrs={"data-event-id": event_id}, children={"calendar__event": _FakeQuery(text=title), **trigger_children})
    return row


class _FakeDetailPage:
    def __init__(self, rows, body_text_after_click=""):
        self._rows = rows
        self._body_text_after_click = body_text_after_click

    async def goto(self, *args, **kwargs):
        return None

    async def query_selector_all(self, selector):
        if "data-event-id" in selector:
            return self._rows
        return []

    async def inner_text(self, selector=None):
        return self._body_text_after_click

    async def wait_for_timeout(self, _ms):
        return None


def test_event_detail_realistic_fixture_parses_expected_fields(patched_launch_page):
    row = _detail_row("150103", "ISM Services PMI")
    body = "Source\nInstitute for Supply Management\nWhy Traders Care\nIt is a leading indicator of economic health.\nFrequency\nMonthly"
    patched_launch_page(ff_detail, _FakeDetailPage([row], body_text_after_click=body))
    definition, meta = asyncio.run(ff_detail.fetch_event_detail_by_name("ism services pmi", None, CFG))
    assert meta["status"] in {"ok", "partial"}
    assert definition["event_name"] == "ISM Services PMI"
    if meta["status"] == "ok":
        assert definition["source_name"] == "Institute for Supply Management"
        assert definition["frequency"] == "Monthly"


def test_event_detail_not_found_on_month_calendar(patched_launch_page):
    row = _detail_row("150103", "Some Other Event")
    patched_launch_page(ff_detail, _FakeDetailPage([row]))
    definition, meta = asyncio.run(ff_detail.fetch_event_detail_by_name("nonexistent event xyz", None, CFG))
    assert definition is None
    assert meta["status"] == "not_found"


def test_event_detail_playwright_unavailable_is_not_a_crash(monkeypatch):
    from backend.economic_intelligence.providers import _browser

    async def _raise_unavailable(**kwargs):
        raise PlaywrightUnavailableError("no playwright")
        yield  # pragma: no cover

    monkeypatch.setattr(_browser, "launch_page", _raise_unavailable)
    monkeypatch.setattr(ff_detail, "launch_page", _raise_unavailable)
    definition, meta = asyncio.run(ff_detail.fetch_event_detail_by_name("some event", None, CFG))
    assert definition is None
    assert meta["status"] == "unavailable"


def test_event_detail_cache_hit_avoids_provider_call(monkeypatch):
    from backend.economic_intelligence import service

    monkeypatch.setattr(service, "query_events", lambda **kwargs: [{"normalized_name": "cached event", "scheduled_at_utc": "2026-01-01T00:00:00+00:00"}])
    monkeypatch.setattr(service, "is_definition_stale", lambda name, stale_after_days: False)

    called = {"fetched": False}

    async def _should_not_be_called(*args, **kwargs):
        called["fetched"] = True
        return None, {"status": "ok"}

    monkeypatch.setattr(service, "fetch_event_detail_by_name", _should_not_be_called)
    monkeypatch.setattr(service.provider_health, "record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(service, "log_definition_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(service.provider_health, "record_success", lambda *a, **k: None)
    result = asyncio.run(service.economic_intelligence_service._refresh_stale_event_details())
    assert called["fetched"] is False
    assert result["cache_hits"] == 1
    assert result["cache_misses"] == 0


def test_event_detail_stale_cache_triggers_refresh(monkeypatch):
    from backend.economic_intelligence import service

    monkeypatch.setattr(service, "query_events", lambda **kwargs: [{"normalized_name": "stale event", "scheduled_at_utc": "2026-01-01T00:00:00+00:00"}])
    monkeypatch.setattr(service, "is_definition_stale", lambda name, stale_after_days: True)

    called = {"fetched": False}

    async def _fake_fetch(*args, **kwargs):
        called["fetched"] = True
        return {"event_name": "Stale Event", "source_hash": "abc"}, {"status": "ok"}

    monkeypatch.setattr(service, "fetch_event_detail_by_name", _fake_fetch)
    monkeypatch.setattr(service, "upsert_definition", lambda d: {"status": "updated"})
    monkeypatch.setattr(service.provider_health, "record_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(service, "log_definition_cache_event", lambda *a, **k: None)
    monkeypatch.setattr(service.provider_health, "record_success", lambda *a, **k: None)
    result = asyncio.run(service.economic_intelligence_service._refresh_stale_event_details())
    assert called["fetched"] is True
    assert result["cache_misses"] == 1


def test_failed_detail_scrape_does_not_affect_calendar_guard():
    """Event details are enrichment only -- calendar_guard.evaluate() never reads from
    EconomicEventDefinitionORM/definition_for() at all, so a total detail-provider outage
    cannot change a calendar decision."""
    from backend.economic_intelligence import calendar_guard

    events = [{"currency": "USD", "impact": "high", "scheduled_at_utc": "2026-08-05T12:10:00+00:00", "is_central_bank_event": False}]
    result = calendar_guard.evaluate(["USD"], __import__("datetime").datetime(2026, 8, 5, 12, 0, tzinfo=__import__("datetime").timezone.utc), events, CFG)
    assert result["decision"] == "BLOCK"  # unaffected by any definition data, which was never consulted


def test_manual_refresh_detail_route_calls_provider(monkeypatch):
    from backend.economic_intelligence import service

    monkeypatch.setattr(service, "get_event", lambda event_id: {"normalized_name": "manual refresh event", "scheduled_at_utc": "2026-01-01T00:00:00+00:00"})
    called = {}

    async def _fake_fetch(name, scheduled_at, config):
        called["name"] = name
        return {"event_name": "Manual Refresh Event", "source_hash": "xyz"}, {"status": "ok"}

    monkeypatch.setattr(service, "fetch_event_detail_by_name", _fake_fetch)
    monkeypatch.setattr(service, "upsert_definition", lambda d: {"status": "created"})
    monkeypatch.setattr(service, "log_definition_cache_event", lambda *a, **k: None)
    result = asyncio.run(service.economic_intelligence_service.refresh_event_detail_for_event_id("EVT123"))
    assert called["name"] == "manual refresh event"
    assert result["status"] == "ok"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.services.crypto_market_service import CryptoMarketService


class _FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def build_key(self, data_type: str, symbol: str, params: dict | None = None) -> str:
        return f"{data_type}:{symbol}:{params or {}}"

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value, ttl: int = 300):  # noqa: ARG002
        self.data[key] = value


class _FakeFetcher:
    """No crypto quote-universe/candle source is wired into UnifiedFetcher anymore (the Yahoo
    Finance endpoints this used to call were removed, with no replacement) -- CryptoMarketService
    now only ever reads from cache/stale-cache. This stub stands in for the fetcher_factory
    signature without needing to expose any live-source methods."""


_UNIVERSE_ROW = {
    "symbol": "BTC-USD",
    "name": "Bitcoin",
    "price": 50000,
    "change_24h": 2.1,
    "volume_24h": 1000,
    "market_cap": 50000000,
    "sector": "L1",
    "day_high": 51000,
    "day_low": 49000,
}

_UNIVERSE_ROW_ETH = {
    "symbol": "ETH-USD",
    "name": "Ethereum",
    "price": 3000,
    "change_24h": -1.2,
    "volume_24h": 800,
    "market_cap": 2400000,
    "sector": "L1",
    "day_high": 3200,
    "day_low": 2800,
}


def test_crypto_service_market_reads_from_cache() -> None:
    cache = _FakeCache()
    cache_key = cache.build_key("crypto_quotes", "universe", {"limit": 300})
    cache.data[cache_key] = [_UNIVERSE_ROW, _UNIVERSE_ROW_ETH]

    async def _fetcher():
        return _FakeFetcher()

    service = CryptoMarketService(cache_backend=cache, fetcher_factory=_fetcher)
    first = asyncio.run(service.markets(limit=10))
    second = asyncio.run(service.markets(limit=10))

    assert first["items"]
    assert second["items"]


def test_crypto_service_market_filter_and_sort() -> None:
    cache = _FakeCache()
    cache_key = cache.build_key("crypto_quotes", "universe", {"limit": 300})
    cache.data[cache_key] = [_UNIVERSE_ROW, _UNIVERSE_ROW_ETH]

    async def _fetcher():
        return _FakeFetcher()

    service = CryptoMarketService(cache_backend=cache, fetcher_factory=_fetcher)
    result = asyncio.run(
        service.markets(
            limit=10,
            q="ETH",
            sector="L1",
            sort_by="change_24h",
            sort_order="asc",
        )
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["symbol"] == "ETH-USD"


def test_crypto_service_coin_detail_shape() -> None:
    cache = _FakeCache()
    cache_key = cache.build_key("crypto_quotes", "universe", {"limit": 300})
    cache.data[cache_key] = [_UNIVERSE_ROW]

    async def _fetcher():
        return _FakeFetcher()

    service = CryptoMarketService(
        cache_backend=cache,
        fetcher_factory=_fetcher,
        now_factory=lambda: datetime(2026, 3, 5, tzinfo=timezone.utc),
    )
    detail = asyncio.run(service.coin_detail("btc"))
    assert detail is not None
    assert detail["symbol"] == "BTC-USD"
    assert detail["high_24h"] == 51000
    assert detail["low_24h"] == 49000
    # No crypto candle source is wired in (core/crypto_adapter.py always returns an empty
    # chart payload now) -- the sparkline degrades to empty rather than fabricating one.
    assert detail["sparkline"] == []


def test_crypto_service_uses_stale_cache_when_universe_cache_is_empty() -> None:
    cache = _FakeCache()
    stale_payload = [
        {
            "symbol": "BTC-USD",
            "name": "Bitcoin",
            "price": 50000,
            "change_24h": 1.2,
            "volume_24h": 1000,
            "market_cap": 50000000,
            "sector": "L1",
            "day_high": 51000,
            "day_low": 49000,
        }
    ]
    stale_key = cache.build_key("crypto_quotes", "universe_stale", {"limit": 300})
    cache.data[stale_key] = stale_payload

    async def _fetcher():
        return _FakeFetcher()

    service = CryptoMarketService(cache_backend=cache, fetcher_factory=_fetcher)
    result = asyncio.run(service.markets(limit=10))

    assert len(result["items"]) == 1
    assert result["items"][0]["symbol"] == "BTC-USD"


def test_crypto_service_market_empty_when_no_cache_available() -> None:
    # No crypto quote-universe source is wired in anymore -- with neither the live cache nor
    # the stale-cache rescue populated, markets() degrades to an empty universe rather than
    # raising.
    cache = _FakeCache()

    async def _fetcher():
        return _FakeFetcher()

    service = CryptoMarketService(cache_backend=cache, fetcher_factory=_fetcher)
    result = asyncio.run(service.markets(limit=10))

    assert result["items"] == []

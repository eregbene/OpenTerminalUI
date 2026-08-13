from __future__ import annotations

import asyncio

from backend.api.routes import crypto
from backend.realtime.binance_ws import get_binance_derivatives_state


def _quotes_payload() -> list[dict]:
    return [
        {"symbol": "BTC-USD", "regularMarketPrice": 50000, "regularMarketChangePercent": 2.1, "regularMarketVolume": 1000},
        {"symbol": "ETH-USD", "regularMarketPrice": 3000, "regularMarketChangePercent": 1.5, "regularMarketVolume": 800},
        {"symbol": "UNI-USD", "regularMarketPrice": 12, "regularMarketChangePercent": -1.2, "regularMarketVolume": 3200},
        {"symbol": "AAVE-USD", "regularMarketPrice": 95, "regularMarketChangePercent": 3.4, "regularMarketVolume": 700},
        {"symbol": "DOGE-USD", "regularMarketPrice": 0.18, "regularMarketChangePercent": -3.1, "regularMarketVolume": 25000},
    ]


def _fixed_rows() -> list["crypto._Row"]:
    rows = []
    for item in _quotes_payload():
        symbol = item["symbol"]
        meta = crypto._CRYPTO_META.get(symbol, {})
        price = float(item["regularMarketPrice"])
        volume = float(item["regularMarketVolume"])
        rows.append(
            crypto._Row(
                symbol=symbol,
                name=str(meta.get("name") or symbol),
                price=price,
                change_24h=float(item["regularMarketChangePercent"]),
                volume_24h=volume,
                market_cap=max(price * max(volume, 1.0), price * 1_000_000.0),
                sector=str(meta.get("sector") or "Other"),
            )
        )
    return rows


def _patch_fetcher(monkeypatch):
    """No crypto quote-universe/candle source is wired into UnifiedFetcher anymore (the Yahoo
    Finance endpoints this used to call were removed, with no replacement) -- _load_rows now
    only ever reads from cache. Every endpoint test below only cares that _load_rows returns a
    stable dataset, not how -- so patch it directly rather than faking a live source. The two
    tests that specifically exercise _load_rows' own cache/stale-cache behavior seed the real
    cache instead (see test_crypto_markets_reuses_cached_universe_across_requests /
    test_crypto_markets_uses_stale_cache_when_universe_cache_empty)."""

    async def _fake_load_rows(limit: int = 100):  # noqa: ARG001
        return _fixed_rows()

    monkeypatch.setattr(crypto, "_load_rows", _fake_load_rows)


def _clear_crypto_quote_cache(limit: int) -> None:
    key = crypto.cache_instance.build_key("crypto_quotes", "universe", {"limit": limit})
    stale_key = crypto.cache_instance.build_key("crypto_quotes", "universe_stale", {"limit": limit})

    crypto.cache_instance._l1_cache.pop(key, None)
    crypto.cache_instance._l1_cache.pop(stale_key, None)

    if crypto.cache_instance._redis:
        asyncio.run(crypto.cache_instance._redis.delete(key, stale_key))

    if crypto.cache_instance._db_conn:
        with crypto.cache_instance._db_lock:
            crypto.cache_instance._db_conn.execute("DELETE FROM cache WHERE key IN (?, ?)", (key, stale_key))
            crypto.cache_instance._db_conn.commit()


def test_crypto_search_returns_matches() -> None:
    # search() is a static instrument-list lookup with no data-source dependency at all.
    result = asyncio.run(crypto.search_crypto(q="btc", limit=10))
    assert any(item["symbol"] == "BTC-USD" for item in result["items"])


def test_crypto_candles_returns_404_with_no_source_wired_in() -> None:
    # No crypto OHLCV source is wired in (core/crypto_adapter.py's candles() always returns an
    # empty payload now, the Yahoo Finance chart endpoint it used to proxy through was removed
    # with no replacement) -- this endpoint always 404s rather than fabricating bars.
    from fastapi import HTTPException

    try:
        asyncio.run(crypto.crypto_candles(symbol="BTC-USD", interval="1d", range="1y"))
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


def test_crypto_markets_returns_normalized_items(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_markets(limit=10))
    assert "items" in result
    assert result["items"][0]["symbol"] in {"BTC-USD", "ETH-USD", "UNI-USD", "AAVE-USD", "DOGE-USD"}
    assert "count" in result


def test_crypto_markets_reuses_cached_universe_across_requests() -> None:
    # No crypto quote-universe source is wired into UnifiedFetcher anymore -- _load_rows now
    # only ever reads from cache, so pre-seed it directly and confirm repeated requests return
    # the identical cached payload without needing any live source.
    _clear_crypto_quote_cache(limit=17)
    cache_key = crypto.cache_instance.build_key("crypto_quotes", "universe", {"limit": 17})
    seeded = [
        {
            "symbol": "BTC-USD",
            "name": "Bitcoin",
            "price": 50000,
            "change_24h": 2.1,
            "volume_24h": 1000,
            "market_cap": 50000000,
            "sector": "L1",
        }
    ]
    asyncio.run(crypto.cache_instance.set(cache_key, seeded, ttl=300))

    first = asyncio.run(crypto.crypto_markets(limit=17))
    second = asyncio.run(crypto.crypto_markets(limit=17))

    assert first["items"] == second["items"]
    assert first["items"][0]["symbol"] == "BTC-USD"


def test_crypto_markets_supports_filter_and_sort(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_markets(limit=10, q="eth", sector="l1", sort_by="change_24h", sort_order="asc"))
    assert len(result["items"]) == 1
    assert result["items"][0]["symbol"] == "ETH-USD"


def test_crypto_movers_gainers_sorted_desc(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_movers(metric="gainers", limit=5))
    assert result["items"][0]["symbol"] == "AAVE-USD"


def test_crypto_dominance_fields_exist(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_dominance())
    assert "btc_pct" in result and "eth_pct" in result and "others_pct" in result
    total = result["btc_pct"] + result["eth_pct"] + result["others_pct"]
    assert 99.0 <= total <= 101.0


def test_crypto_heatmap_has_buckets_and_depth(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_heatmap(limit=5))
    assert len(result["items"]) >= 2
    first = result["items"][0]
    assert first["bucket"] in {"surge", "bullish", "up", "flat", "down", "bearish", "flush"}
    assert -1.0 <= float(first["depth_imbalance"]) <= 1.0
    assert float(first["depth_bid_notional"]) > 0
    assert float(first["depth_ask_notional"]) > 0


def test_crypto_derivatives_aggregates_liquidations(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    state = get_binance_derivatives_state()
    state.reset()

    result = asyncio.run(crypto.crypto_derivatives(limit=4))
    assert len(result["items"]) >= 2
    assert result["totals"]["liquidations_24h"] == (
        result["totals"]["long_liquidations_24h"] + result["totals"]["short_liquidations_24h"]
    )
    assert any(item["funding_rate_8h"] != 0 for item in result["items"])


def test_crypto_defi_dashboard_headline_and_protocols(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_defi_dashboard())
    assert result["headline"]["tvl_usd"] > 0
    assert result["headline"]["dex_volume_24h"] > 0
    assert result["protocols"]
    assert all(row["symbol"].endswith("-USD") for row in result["protocols"])


def test_crypto_correlation_matrix_is_symmetric_and_bounded(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    result = asyncio.run(crypto.crypto_correlation_matrix(window=12, limit=4))
    symbols = result["symbols"]
    matrix = result["matrix"]
    assert len(symbols) == 4
    assert len(matrix) == 4

    for i in range(4):
        assert abs(float(matrix[i][i]) - 1.0) < 1e-9
        for j in range(4):
            val = float(matrix[i][j])
            assert -1.0 <= val <= 1.0
            assert abs(float(matrix[i][j]) - float(matrix[j][i])) < 1e-9


def test_crypto_coin_detail_shape(monkeypatch) -> None:
    _patch_fetcher(monkeypatch)
    # crypto_coin_detail routes through market_service (CryptoMarketService), a separate
    # implementation from crypto.py's own _load_rows -- it shares the same underlying
    # cache_instance though, so seed that directly rather than patching a second code path.
    cache_key = crypto.cache_instance.build_key("crypto_quotes", "universe", {"limit": 300})
    asyncio.run(
        crypto.cache_instance.set(
            cache_key,
            [
                {
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
            ],
            ttl=300,
        )
    )
    detail = asyncio.run(crypto.crypto_coin_detail("btc"))
    assert detail["symbol"] == "BTC-USD"
    assert detail["name"] == "Bitcoin"
    assert "high_24h" in detail and "low_24h" in detail
    assert isinstance(detail["sparkline"], list)


def test_crypto_markets_uses_stale_cache_when_universe_cache_empty() -> None:
    # No crypto quote-universe source is wired into UnifiedFetcher anymore -- with the primary
    # cache empty, _load_rows falls back to the stale cache rather than raising.
    crypto.cache_instance._l1_cache.clear()
    stale_key = crypto.cache_instance.build_key("crypto_quotes", "universe_stale", {"limit": 12})
    asyncio.run(
        crypto.cache_instance.set(
            stale_key,
            [
                {
                    "symbol": "BTC-USD",
                    "name": "Bitcoin",
                    "price": 50000,
                    "change_24h": 1.5,
                    "volume_24h": 1000,
                    "market_cap": 50000000,
                    "sector": "L1",
                }
            ],
            ttl=300,
        )
    )

    result = asyncio.run(crypto.crypto_markets(limit=12))
    assert len(result["items"]) == 1
    assert result["items"][0]["symbol"] == "BTC-USD"

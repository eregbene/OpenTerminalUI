from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import forex as forex_routes
from backend.services.forex_service import ForexService, SUPPORTED_CURRENCIES

FIXED_NOW = datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)


class _FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.get_calls = 0
        self.set_calls = 0

    def build_key(self, data_type: str, symbol: str, params: dict | None = None) -> str:
        serialized = json.dumps(params or {}, sort_keys=True)
        return f"{data_type}:{symbol}:{serialized}"

    async def get(self, key: str):
        self.get_calls += 1
        return self.data.get(key)

    async def set(self, key: str, value, ttl: int = 300):  # noqa: ANN001, ARG002
        self.set_calls += 1
        self.data[key] = value


class _FakeFinnhub:
    def __init__(
        self,
        *,
        rates_payload: dict | None = None,
        candle_payloads: dict[str, dict | Exception] | None = None,
        raise_rates: Exception | None = None,
    ) -> None:
        self._rates_payload = rates_payload or {}
        self._candle_payloads = candle_payloads or {}
        self._raise_rates = raise_rates
        self.rate_calls = 0
        self.candle_calls: list[tuple[str, str, int, int]] = []

    async def get_forex_rates(self, base_currency: str):
        self.rate_calls += 1
        assert base_currency == "USD"
        if self._raise_rates is not None:
            raise self._raise_rates
        return self._rates_payload

    async def get_forex_candles(self, symbol: str, resolution: str, from_ts: int, to_ts: int):
        self.candle_calls.append((symbol, resolution, from_ts, to_ts))
        payload = self._candle_payloads.get(symbol)
        if isinstance(payload, Exception):
            raise payload
        return payload or {"s": "no_data"}


def _finnhub_rates_payload() -> dict:
    return {
        "base": "USD",
        "quote": {
            "EUR": 0.92,
            "GBP": 0.785,
            "JPY": 151.0,
            "CHF": 0.885,
            "AUD": 1.51,
            "CAD": 1.36,
            "NZD": 1.67,
        },
    }


def _finnhub_candle_payload(*, close_series: list[float]) -> dict:
    return {
        "s": "ok",
        "t": [1711065600, 1711152000, 1711238400],
        "o": [round(value - 0.0009, 6) for value in close_series],
        "h": [round(value + 0.0014, 6) for value in close_series],
        "l": [round(value - 0.0016, 6) for value in close_series],
        "c": close_series,
        "v": [900, 950, 975],
    }


def _build_service(
    *,
    finnhub: _FakeFinnhub | None = None,
    cache: _FakeCache | None = None,
) -> ForexService:
    return ForexService(
        finnhub=finnhub,
        cache_backend=cache or _FakeCache(),
        now_factory=lambda: FIXED_NOW,
    )


def _build_app(service: ForexService) -> FastAPI:
    app = FastAPI()
    forex_routes.service = service
    app.include_router(forex_routes.router, prefix="/api")
    return app


def test_cross_rates_endpoint_returns_8x8_matrix_from_finnhub_rates() -> None:
    cache = _FakeCache()
    finnhub = _FakeFinnhub(rates_payload=_finnhub_rates_payload())
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=cache)))

    response = client.get("/api/forex/cross-rates")

    assert response.status_code == 200
    body = response.json()
    assert body["currencies"] == SUPPORTED_CURRENCIES
    assert len(body["matrix"]) == 8
    assert all(len(row) == 8 for row in body["matrix"])
    eur_index = body["currencies"].index("EUR")
    usd_index = body["currencies"].index("USD")
    assert abs(body["matrix"][eur_index][usd_index] - (1 / 0.92)) < 1e-6
    assert body["pair_quotes"]["EURUSD"]["symbol"] == "EURUSD=X"
    assert finnhub.rate_calls == 1
    assert cache.set_calls == 2


def test_cross_rates_endpoint_raises_when_finnhub_rates_unavailable() -> None:
    finnhub = _FakeFinnhub(raise_rates=RuntimeError("finnhub unavailable"))
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=_FakeCache())))

    response = client.get("/api/forex/cross-rates")

    assert response.status_code == 502


def test_pair_chart_endpoint_returns_finnhub_ohlcv_and_normalizes_pair() -> None:
    finnhub = _FakeFinnhub(
        candle_payloads={"OANDA:EUR_USD": _finnhub_candle_payload(close_series=[1.0810, 1.0830, 1.0850])}
    )
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=_FakeCache())))

    response = client.get("/api/forex/pairs/eur/usd?interval=1d&range=1mo")

    assert response.status_code == 200
    body = response.json()
    assert body["pair"] == "EURUSD"
    assert body["source_symbol"] == "OANDA:EUR_USD"
    assert body["interval"] == "1d"
    assert len(body["candles"]) == 3
    assert body["current_rate"] == 1.085
    assert len(finnhub.candle_calls) == 1
    assert finnhub.candle_calls[0][0] == "OANDA:EUR_USD"
    assert finnhub.candle_calls[0][1] == "D"


def test_major_forex_endpoints_expose_canonical_symbols() -> None:
    finnhub = _FakeFinnhub(
        rates_payload=_finnhub_rates_payload(),
        candle_payloads={
            "OANDA:USD_JPY": _finnhub_candle_payload(close_series=[151.1, 151.2, 151.3]),
            "OANDA:XAU_USD": _finnhub_candle_payload(close_series=[2351.0, 2352.0, 2353.0]),
        },
    )
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=_FakeCache())))

    instruments = client.get("/api/forex/instruments")
    quotes = client.get("/api/forex/quotes")
    candles = client.get("/api/forex/candles/USDJPY?interval=1d&range=1mo")

    assert instruments.status_code == 200
    assert instruments.json()["symbols"] == ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD"]
    assert quotes.status_code == 200
    assert {row["symbol"] for row in quotes.json()["quotes"]} == set(instruments.json()["symbols"])
    gold = next(row for row in quotes.json()["quotes"] if row["symbol"] == "XAUUSD")
    # provider_symbol is a static ticker-notation label (FOREX_INSTRUMENTS), independent of
    # which live data provider is actually queried -- unaffected by the Yahoo client removal.
    assert gold["provider_symbol"] == "GC=F"
    assert gold["is_proxy"] is True
    assert candles.status_code == 200
    assert candles.json()["pair"] == "USDJPY"
    assert candles.json()["source_symbol"] == "OANDA:USD_JPY"


def test_stock_snapshot_forex_pair_bypasses_equity_fundamentals() -> None:
    from backend.api.routes import stocks

    payload = asyncio.run(stocks.get_stock("EURUSD", market="NSE"))

    assert payload.ticker == "EURUSD"
    assert payload.symbol == "EURUSD=X"
    assert payload.exchange == "FX"
    assert payload.raw["asset_class"] == "forex"


def test_pair_chart_endpoint_falls_back_to_inverse_symbol_when_direct_has_no_data() -> None:
    finnhub = _FakeFinnhub(
        candle_payloads={"OANDA:USD_EUR": _finnhub_candle_payload(close_series=[0.9231, 0.9219, 0.9208])}
    )
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=_FakeCache())))

    response = client.get("/api/forex/pairs/EURUSD")

    assert response.status_code == 200
    body = response.json()
    assert body["pair"] == "EURUSD"
    assert body["source_symbol"] == "OANDA:USD_EUR"
    assert len(body["candles"]) == 3
    assert len(finnhub.candle_calls) == 2  # direct OANDA:EUR_USD (no data) then inverse


def test_pair_chart_endpoint_reuses_cache_on_repeated_requests() -> None:
    cache = _FakeCache()
    finnhub = _FakeFinnhub(
        candle_payloads={"OANDA:GBP_USD": _finnhub_candle_payload(close_series=[1.272, 1.273, 1.274])}
    )
    client = TestClient(_build_app(_build_service(finnhub=finnhub, cache=cache)))

    first = client.get("/api/forex/pairs/GBPUSD")
    second = client.get("/api/forex/pairs/GBPUSD")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["current_rate"] == second.json()["current_rate"] == 1.274
    assert len(finnhub.candle_calls) == 1
    assert cache.set_calls == 2


def test_pair_chart_endpoint_rejects_invalid_pair() -> None:
    client = TestClient(_build_app(_build_service(finnhub=_FakeFinnhub(), cache=_FakeCache())))

    response = client.get("/api/forex/pairs/ABC")

    assert response.status_code == 400
    assert "6-letter FX symbol" in response.json()["detail"]


def test_central_banks_endpoint_returns_rate_decision_calendar() -> None:
    client = TestClient(_build_app(_build_service(finnhub=_FakeFinnhub(), cache=_FakeCache())))

    response = client.get("/api/forex/central-banks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["banks"]) == 8
    assert body["banks"][0]["bank"] == "Federal Reserve"
    assert body["banks"][0]["currency"] == "USD"
    assert body["banks"][0]["policy_rate"] == 5.25
    assert body["banks"][0]["days_since_last_decision"] == 32


def test_service_uses_stale_pair_cache_when_live_sources_fail() -> None:
    cache = _FakeCache()
    stale_key = cache.build_key("forex_pair_chart_stale", "EURUSD", {"interval": "1d", "range": "3mo"})
    cache.data[stale_key] = {
        "pair": "EURUSD",
        "source_symbol": "stale-cache",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "interval": "1d",
        "market": "FX",
        "as_of": FIXED_NOW,
        "current_rate": 1.083,
        "candles": [{"t": 1711238400, "o": 1.08, "h": 1.084, "l": 1.079, "c": 1.083, "v": 0}],
    }
    service = _build_service(
        finnhub=_FakeFinnhub(candle_payloads={"OANDA:EUR_USD": RuntimeError("finnhub unavailable")}),
        cache=cache,
    )

    payload = asyncio.run(service.get_pair_chart("EURUSD"))

    assert payload["source_symbol"] == "stale-cache"
    assert payload["current_rate"] == 1.083

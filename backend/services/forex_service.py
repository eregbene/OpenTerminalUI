from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from backend.core.finnhub_client import FinnhubClient
from backend.forex_intelligence.instruments import FOREX_INSTRUMENTS, SUPPORTED_FOREX_CURRENCIES, get_forex_instrument
from backend.shared.cache import cache as cache_instance

SUPPORTED_CURRENCIES = list(SUPPORTED_FOREX_CURRENCIES)
DEFAULT_PAIR_INTERVAL = "1d"
DEFAULT_PAIR_RANGE = "3mo"

_PAIR_RANGE_TO_DELTA: dict[str, timedelta] = {
    "1d": timedelta(days=1),
    "5d": timedelta(days=5),
    "1mo": timedelta(days=31),
    "3mo": timedelta(days=93),
    "6mo": timedelta(days=186),
    "1y": timedelta(days=366),
    "2y": timedelta(days=732),
    "5y": timedelta(days=366 * 5),
}

_FINNHUB_RESOLUTION_MAP: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "D",
    "1wk": "W",
    "1mo": "M",
}

_CENTRAL_BANK_SNAPSHOTS: list[dict[str, Any]] = [
    {
        "currency": "USD",
        "bank": "Federal Reserve",
        "policy_rate": 5.25,
        "last_decision_date": date(2026, 2, 18),
        "next_decision_date": date(2026, 3, 31),
        "last_action": "Hold",
        "last_change_bps": 0,
        "decision_cycle": "6 weeks",
    },
    {
        "currency": "EUR",
        "bank": "European Central Bank",
        "policy_rate": 3.00,
        "last_decision_date": date(2026, 3, 5),
        "next_decision_date": date(2026, 4, 16),
        "last_action": "Cut",
        "last_change_bps": -25,
        "decision_cycle": "6 weeks",
    },
    {
        "currency": "GBP",
        "bank": "Bank of England",
        "policy_rate": 4.50,
        "last_decision_date": date(2026, 3, 19),
        "next_decision_date": date(2026, 5, 7),
        "last_action": "Hold",
        "last_change_bps": 0,
        "decision_cycle": "6 weeks",
    },
    {
        "currency": "JPY",
        "bank": "Bank of Japan",
        "policy_rate": 0.25,
        "last_decision_date": date(2026, 3, 19),
        "next_decision_date": date(2026, 4, 30),
        "last_action": "Hike",
        "last_change_bps": 10,
        "decision_cycle": "2 months",
    },
    {
        "currency": "CHF",
        "bank": "Swiss National Bank",
        "policy_rate": 1.25,
        "last_decision_date": date(2026, 3, 14),
        "next_decision_date": date(2026, 6, 13),
        "last_action": "Hold",
        "last_change_bps": 0,
        "decision_cycle": "Quarterly",
    },
    {
        "currency": "AUD",
        "bank": "Reserve Bank of Australia",
        "policy_rate": 4.10,
        "last_decision_date": date(2026, 3, 3),
        "next_decision_date": date(2026, 4, 7),
        "last_action": "Hold",
        "last_change_bps": 0,
        "decision_cycle": "Monthly",
    },
    {
        "currency": "CAD",
        "bank": "Bank of Canada",
        "policy_rate": 4.00,
        "last_decision_date": date(2026, 3, 12),
        "next_decision_date": date(2026, 4, 23),
        "last_action": "Cut",
        "last_change_bps": -25,
        "decision_cycle": "6 weeks",
    },
    {
        "currency": "NZD",
        "bank": "Reserve Bank of New Zealand",
        "policy_rate": 5.50,
        "last_decision_date": date(2026, 2, 25),
        "next_decision_date": date(2026, 4, 8),
        "last_action": "Hold",
        "last_change_bps": 0,
        "decision_cycle": "6 weeks",
    },
]


def _normalize_pair_text(pair: str) -> str:
    return "".join(ch for ch in str(pair).upper() if "A" <= ch <= "Z")


def _yahoo_symbol(base_currency: str, quote_currency: str) -> str:
    canonical = f"{base_currency}{quote_currency}"
    if canonical in FOREX_INSTRUMENTS:
        return str(get_forex_instrument(canonical).provider_symbol_mappings["yahoo"])
    return f"{base_currency}{quote_currency}=X"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if result != result:
            return default
        return result
    except Exception:
        return default


def _parse_finnhub_candles(payload: dict[str, Any], *, invert: bool = False) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    status = str(payload.get("s") or "").lower()
    if status and status != "ok":
        return []

    timestamps = payload.get("t")
    opens = payload.get("o")
    highs = payload.get("h")
    lows = payload.get("l")
    closes = payload.get("c")
    volumes = payload.get("v")
    if not all(isinstance(series, list) for series in (timestamps, opens, highs, lows, closes)):
        return []

    if not isinstance(volumes, list):
        volumes = [0] * len(timestamps)

    length = min(len(timestamps), len(opens), len(highs), len(lows), len(closes), len(volumes))
    candles: list[dict[str, Any]] = []
    for idx in range(length):
        timestamp = timestamps[idx]
        open_price = _f(opens[idx], -1)
        high_price = _f(highs[idx], -1)
        low_price = _f(lows[idx], -1)
        close_price = _f(closes[idx], -1)
        volume = int(_f(volumes[idx], 0))
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue

        if invert:
            candles.append(
                {
                    "t": int(timestamp),
                    "o": round(1.0 / open_price, 6),
                    "h": round(1.0 / low_price, 6),
                    "l": round(1.0 / high_price, 6),
                    "c": round(1.0 / close_price, 6),
                    "v": volume,
                }
            )
        else:
            candles.append(
                {
                    "t": int(timestamp),
                    "o": round(open_price, 6),
                    "h": round(high_price, 6),
                    "l": round(low_price, 6),
                    "c": round(close_price, 6),
                    "v": volume,
                }
            )
    return candles


@dataclass(frozen=True)
class PairResolution:
    interval: str
    range_str: str


class ForexService:
    def __init__(
        self,
        finnhub: FinnhubClient | None = None,
        cache_backend: Any = cache_instance,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._finnhub = finnhub or FinnhubClient()
        self._cache = cache_backend
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        now = self._now_factory()
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    def _forex_market_open(self) -> bool:
        return self._now().weekday() < 5

    def _quote_ttl(self) -> int:
        return 60 if self._forex_market_open() else 600

    def _chart_ttl(self) -> int:
        return 120 if self._forex_market_open() else 1800

    def _calendar_ttl(self) -> int:
        return 3600 if self._forex_market_open() else 21600

    def _resolve_pair(self, pair: str) -> tuple[str, str, str]:
        normalized = _normalize_pair_text(pair)
        if normalized in FOREX_INSTRUMENTS:
            instrument = get_forex_instrument(normalized)
            return normalized, instrument.base_currency, instrument.quote_currency
        if len(normalized) != 6:
            raise ValueError("pair must resolve to a 6-letter FX symbol, for example EURUSD")
        base_currency = normalized[:3]
        quote_currency = normalized[3:]
        if base_currency not in SUPPORTED_CURRENCIES or quote_currency not in SUPPORTED_CURRENCIES:
            raise ValueError("pair must use supported currency codes: USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD")
        if base_currency == quote_currency:
            raise ValueError("pair must use two different currencies")
        return normalized, base_currency, quote_currency

    def _pair_resolution(self, interval: str | None, range_str: str | None) -> PairResolution:
        normalized_interval = str(interval or DEFAULT_PAIR_INTERVAL).strip().lower() or DEFAULT_PAIR_INTERVAL
        normalized_range = str(range_str or DEFAULT_PAIR_RANGE).strip().lower() or DEFAULT_PAIR_RANGE
        return PairResolution(interval=normalized_interval, range_str=normalized_range)

    async def _finnhub_get_forex_rates(self, base_currency: str) -> dict[str, Any]:
        getter = getattr(self._finnhub, "get_forex_rates", None)
        if callable(getter):
            payload = await getter(base_currency)
            return payload if isinstance(payload, dict) else {}

        internal_get = getattr(self._finnhub, "_get", None)
        if not callable(internal_get):
            return {}
        payload = await internal_get("/forex/rates", {"base": base_currency})
        return payload if isinstance(payload, dict) else {}

    async def _finnhub_get_forex_candles(
        self,
        symbol: str,
        resolution: str,
        from_ts: int,
        to_ts: int,
    ) -> dict[str, Any]:
        getter = getattr(self._finnhub, "get_forex_candles", None)
        if callable(getter):
            payload = await getter(symbol, resolution, from_ts, to_ts)
            return payload if isinstance(payload, dict) else {}

        internal_get = getattr(self._finnhub, "_get", None)
        if not callable(internal_get):
            return {}
        payload = await internal_get(
            "/forex/candle",
            {"symbol": symbol, "resolution": resolution, "from": from_ts, "to": to_ts},
        )
        return payload if isinstance(payload, dict) else {}

    async def _fetch_usd_reference_map(self) -> dict[str, float]:
        # Finnhub is the sole reference-rate source now (previously tried Yahoo quotes first,
        # falling back to Finnhub only for whatever Yahoo didn't cover).
        usd_per_unit: dict[str, float] = {"USD": 1.0}
        fallback = await self._finnhub_get_forex_rates("USD")
        quotes = fallback.get("quote") if isinstance(fallback.get("quote"), dict) else fallback
        if isinstance(quotes, dict):
            for currency in SUPPORTED_CURRENCIES:
                if currency == "USD":
                    continue
                quote_value = _f(quotes.get(currency))
                if quote_value > 0:
                    usd_per_unit[currency] = 1.0 / quote_value

        unresolved = [currency for currency in SUPPORTED_CURRENCIES if currency not in usd_per_unit]
        if unresolved:
            raise RuntimeError(f"Unable to resolve FX reference rates for {', '.join(unresolved)}")
        return usd_per_unit

    async def _build_cross_rates_payload(self) -> dict[str, Any]:
        usd_per_unit = await self._fetch_usd_reference_map()
        currencies = list(SUPPORTED_CURRENCIES)
        matrix: list[list[float]] = []
        pair_quotes: dict[str, dict[str, Any]] = {}

        for base_currency in currencies:
            row: list[float] = []
            for quote_currency in currencies:
                rate = round(usd_per_unit[base_currency] / usd_per_unit[quote_currency], 6)
                row.append(rate)
                if base_currency != quote_currency:
                    pair_code = f"{base_currency}{quote_currency}"
                    pair_quotes[pair_code] = {
                        "pair": pair_code,
                        "symbol": _yahoo_symbol(base_currency, quote_currency),
                        "base_currency": base_currency,
                        "quote_currency": quote_currency,
                        "rate": rate,
                    }
            matrix.append(row)

        return {
            "as_of": self._now(),
            "base_currency": "USD",
            "currencies": currencies,
            "matrix": matrix,
            "pair_quotes": pair_quotes,
        }

    async def _build_pair_chart_payload(self, pair: str, interval: str | None, range_str: str | None) -> dict[str, Any]:
        normalized_pair, base_currency, quote_currency = self._resolve_pair(pair)
        resolution = self._pair_resolution(interval, range_str)
        candles: list[dict[str, Any]] = []
        source_symbol = f"OANDA:{base_currency}_{quote_currency}"

        # Finnhub is the sole chart source now (previously tried Yahoo's direct/inverse chart
        # endpoints first, falling back to Finnhub OANDA-style candles only if both failed).
        finnhub_resolution = _FINNHUB_RESOLUTION_MAP.get(resolution.interval)
        if finnhub_resolution:
            end_at = self._now()
            start_at = end_at - _PAIR_RANGE_TO_DELTA.get(resolution.range_str, _PAIR_RANGE_TO_DELTA[DEFAULT_PAIR_RANGE])
            direct_finnhub = await self._finnhub_get_forex_candles(
                f"OANDA:{base_currency}_{quote_currency}",
                finnhub_resolution,
                int(start_at.timestamp()),
                int(end_at.timestamp()),
            )
            candles = _parse_finnhub_candles(direct_finnhub)
            source_symbol = f"OANDA:{base_currency}_{quote_currency}"

            if not candles:
                inverse_finnhub = await self._finnhub_get_forex_candles(
                    f"OANDA:{quote_currency}_{base_currency}",
                    finnhub_resolution,
                    int(start_at.timestamp()),
                    int(end_at.timestamp()),
                )
                inverse_candles = _parse_finnhub_candles(inverse_finnhub, invert=True)
                if inverse_candles:
                    candles = inverse_candles
                    source_symbol = f"OANDA:{quote_currency}_{base_currency}"

        if not candles:
            raise RuntimeError(f"No FX chart data available for {normalized_pair}")

        current_rate = round(_f(candles[-1].get("c")), 6)
        return {
            "pair": normalized_pair,
            "source_symbol": source_symbol,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "interval": resolution.interval,
            "market": "FX",
            "as_of": datetime.fromtimestamp(int(candles[-1]["t"]), tz=timezone.utc),
            "current_rate": current_rate,
            "candles": candles,
        }

    def _build_central_banks_payload(self) -> dict[str, Any]:
        today = self._now().date()
        banks: list[dict[str, Any]] = []
        for snapshot in _CENTRAL_BANK_SNAPSHOTS:
            next_decision = snapshot["next_decision_date"]
            last_decision = snapshot["last_decision_date"]
            banks.append(
                {
                    **snapshot,
                    "days_since_last_decision": (today - last_decision).days,
                    "days_until_next_decision": (next_decision - today).days,
                }
            )
        return {"as_of": self._now(), "banks": banks}

    async def get_cross_rates(self) -> dict[str, Any]:
        params = {"currencies": SUPPORTED_CURRENCIES}
        key = self._cache.build_key("forex_cross_rates", "majors", params)
        stale_key = self._cache.build_key("forex_cross_rates_stale", "majors", params)
        cached = await self._cache.get(key)
        if isinstance(cached, dict):
            return cached

        try:
            payload = await self._build_cross_rates_payload()
        except Exception:
            stale = await self._cache.get(stale_key)
            if isinstance(stale, dict):
                return stale
            raise

        ttl = self._quote_ttl()
        await self._cache.set(key, payload, ttl=ttl)
        await self._cache.set(stale_key, payload, ttl=max(ttl * 6, ttl))
        return payload

    async def get_pair_chart(
        self,
        pair: str,
        interval: str | None = None,
        range_str: str | None = None,
    ) -> dict[str, Any]:
        normalized_pair, _, _ = self._resolve_pair(pair)
        resolution = self._pair_resolution(interval, range_str)
        params = {"interval": resolution.interval, "range": resolution.range_str}
        key = self._cache.build_key("forex_pair_chart", normalized_pair, params)
        stale_key = self._cache.build_key("forex_pair_chart_stale", normalized_pair, params)

        cached = await self._cache.get(key)
        if isinstance(cached, dict):
            return cached

        try:
            payload = await self._build_pair_chart_payload(normalized_pair, resolution.interval, resolution.range_str)
        except Exception:
            stale = await self._cache.get(stale_key)
            if isinstance(stale, dict):
                return stale
            raise

        ttl = self._chart_ttl()
        await self._cache.set(key, payload, ttl=ttl)
        await self._cache.set(stale_key, payload, ttl=max(ttl * 6, ttl))
        return payload

    async def get_central_banks(self) -> dict[str, Any]:
        key = self._cache.build_key("forex_central_banks", "calendar", {"currencies": SUPPORTED_CURRENCIES})
        cached = await self._cache.get(key)
        if isinstance(cached, dict):
            return cached

        payload = self._build_central_banks_payload()
        await self._cache.set(key, payload, ttl=self._calendar_ttl())
        return payload


service = ForexService()

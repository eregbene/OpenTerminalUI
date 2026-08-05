from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ForexInstrument:
    symbol: str
    display_name: str
    base_currency: str
    quote_currency: str
    asset_type: str
    instrument_class: str
    pip_size: Decimal
    point_size: Decimal
    pip_precision: int
    price_precision: int
    display_precision: int
    default_quantity_precision: int
    minimum_price_increment: Decimal
    market_timezone: str
    trading_hours: str
    enabled: bool
    typical_spread_pips: float
    data_source_type: str
    is_proxy: bool
    proxy_for: str | None
    minimum_candle_count: int
    stale_data_seconds: int
    provider_symbol_mappings: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("pip_size", "point_size", "minimum_price_increment"):
            data[key] = str(data[key])
        return data


def _instrument(
    symbol: str,
    base: str,
    quote: str,
    *,
    yahoo: str,
    pip_size: str,
    pip_precision: int,
    price_precision: int,
    spread: float,
    asset_type: str = "forex",
    instrument_class: str = "major_fx",
    point_size: str | None = None,
    display_precision: int | None = None,
    quantity_precision: int = 0,
    price_increment: str | None = None,
    trading_hours: str = "24x5",
    data_source_type: str = "spot_fx",
    is_proxy: bool = False,
    proxy_for: str | None = None,
    provider_mappings: dict[str, Any] | None = None,
) -> ForexInstrument:
    mappings = provider_mappings or {
        "canonical": symbol,
        "twelve_data": f"{base}/{quote}",
        "ibkr": {"secType": "CASH", "symbol": base, "currency": quote, "exchange": "IDEALPRO", "localSymbol": f"{base}.{quote}"},
        "yahoo": yahoo,
        "fixture": symbol,
    }
    return ForexInstrument(
        symbol=symbol,
        display_name=f"{base}/{quote}",
        base_currency=base,
        quote_currency=quote,
        asset_type=asset_type,
        instrument_class=instrument_class,
        pip_size=Decimal(pip_size),
        point_size=Decimal(point_size or pip_size),
        pip_precision=pip_precision,
        price_precision=price_precision,
        display_precision=display_precision if display_precision is not None else price_precision,
        default_quantity_precision=quantity_precision,
        minimum_price_increment=Decimal(price_increment or str(Decimal(pip_size) / Decimal("10"))),
        market_timezone="UTC",
        trading_hours=trading_hours,
        enabled=True,
        typical_spread_pips=spread,
        data_source_type=data_source_type,
        is_proxy=is_proxy,
        proxy_for=proxy_for,
        minimum_candle_count=30,
        stale_data_seconds=180,
        provider_symbol_mappings=mappings,
    )


FOREX_INSTRUMENTS: dict[str, ForexInstrument] = {
    "EURUSD": _instrument("EURUSD", "EUR", "USD", yahoo="EURUSD=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=0.8),
    "GBPUSD": _instrument("GBPUSD", "GBP", "USD", yahoo="GBPUSD=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=1.1),
    "USDJPY": _instrument("USDJPY", "USD", "JPY", yahoo="JPY=X", pip_size="0.01", pip_precision=2, price_precision=3, spread=1.0),
    "USDCHF": _instrument("USDCHF", "USD", "CHF", yahoo="CHF=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=1.2),
    "USDCAD": _instrument("USDCAD", "USD", "CAD", yahoo="CAD=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=1.2),
    "AUDUSD": _instrument("AUDUSD", "AUD", "USD", yahoo="AUDUSD=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=1.0),
    "NZDUSD": _instrument("NZDUSD", "NZD", "USD", yahoo="NZDUSD=X", pip_size="0.0001", pip_precision=4, price_precision=5, spread=1.3),
    "XAUUSD": _instrument(
        "XAUUSD",
        "XAU",
        "USD",
        yahoo="GC=F",
        pip_size="0.10",
        point_size="1.00",
        pip_precision=1,
        price_precision=2,
        display_precision=2,
        price_increment="0.10",
        quantity_precision=3,
        spread=25.0,
        asset_type="commodity",
        instrument_class="precious_metal",
        trading_hours="nearly_24x5_with_metals_breaks",
        data_source_type="gold_futures_proxy",
        is_proxy=True,
        proxy_for="XAUUSD",
        provider_mappings={
            "canonical": "XAUUSD",
            "twelve_data": "XAU/USD",
            "ibkr": {"secType": "CMDTY", "symbol": "XAUUSD", "currency": "USD", "exchange": "SMART", "localSymbol": "XAU.USD", "notes": "spot metal contract requires account/provider support"},
            "yahoo": "GC=F",
            "yahoo_proxy": {"symbol": "GC=F", "instrument_type": "GOLD_FUTURES_PROXY", "is_proxy": True, "proxy_for": "XAUUSD"},
            "fixture": "XAUUSD",
        },
    ),
}

SUPPORTED_FOREX_SYMBOLS = tuple(FOREX_INSTRUMENTS.keys())
SUPPORTED_FOREX_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")
SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


def normalize_forex_symbol(value: str) -> str:
    symbol = "".join(ch for ch in str(value or "").upper() if ch.isalpha())
    if symbol not in FOREX_INSTRUMENTS:
        raise ValueError(f"unsupported forex symbol: {value}")
    return symbol


def get_forex_instrument(symbol: str) -> ForexInstrument:
    return FOREX_INSTRUMENTS[normalize_forex_symbol(symbol)]


def provider_symbol(symbol: str, provider: str) -> Any:
    instrument = get_forex_instrument(symbol)
    try:
        return instrument.provider_symbol_mappings[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported forex provider mapping: {provider}") from exc

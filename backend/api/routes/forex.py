from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.forex_intelligence.instruments import FOREX_INSTRUMENTS, SUPPORTED_FOREX_SYMBOLS, get_forex_instrument, normalize_forex_symbol
from backend.services.forex_service import service as forex_service

router = APIRouter(prefix="/forex", tags=["forex"])

# The packet tests patch this module-level service directly.
service = forex_service


class ForexQuote(BaseModel):
    pair: str
    symbol: str
    base_currency: str
    quote_currency: str
    rate: float


class CrossRatesResponse(BaseModel):
    as_of: datetime
    base_currency: str
    currencies: list[str]
    matrix: list[list[float]]
    pair_quotes: dict[str, ForexQuote]


class ForexCandle(BaseModel):
    t: int = Field(description="Unix timestamp in seconds")
    o: float
    h: float
    l: float
    c: float
    v: int


class PairChartResponse(BaseModel):
    pair: str
    source_symbol: str
    base_currency: str
    quote_currency: str
    interval: str
    market: str
    as_of: datetime
    current_rate: float
    candles: list[ForexCandle]


class ForexInstrumentResponse(BaseModel):
    symbol: str
    display_name: str
    base_currency: str
    quote_currency: str
    asset_type: str
    instrument_class: str
    pip_size: str
    point_size: str
    pip_precision: int
    price_precision: int
    display_precision: int
    default_quantity_precision: int
    minimum_price_increment: str
    market_timezone: str
    trading_hours: str
    enabled: bool
    typical_spread_pips: float
    data_source_type: str
    is_proxy: bool
    proxy_for: str | None = None
    minimum_candle_count: int
    stale_data_seconds: int
    provider_symbol_mappings: dict[str, Any]


class ForexQuoteResponse(BaseModel):
    symbol: str
    display_name: str
    base_currency: str
    quote_currency: str
    price: float
    bid: float | None = None
    ask: float | None = None
    spread_pips: float | None = None
    change: float | None = None
    change_percent: float | None = None
    as_of: datetime
    provider: str
    provider_symbol: str
    delay_status: str
    freshness: str
    quality_status: str
    price_precision: int
    data_source_type: str = "spot_fx"
    is_proxy: bool = False
    proxy_for: str | None = None


class CentralBankSnapshot(BaseModel):
    currency: str
    bank: str
    policy_rate: float
    last_decision_date: date
    next_decision_date: date
    last_action: str
    last_change_bps: int
    days_since_last_decision: int
    days_until_next_decision: int
    decision_cycle: str


class CentralBanksResponse(BaseModel):
    as_of: datetime
    banks: list[CentralBankSnapshot]


@router.get("/cross-rates", response_model=CrossRatesResponse)
async def get_cross_rates() -> Any:
    try:
        return await service.get_cross_rates()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/instruments", response_model=dict[str, Any])
async def get_instruments() -> dict[str, Any]:
    return {
        "asset_class": "forex",
        "symbols": list(SUPPORTED_FOREX_SYMBOLS),
        "instruments": [instrument.payload() for instrument in FOREX_INSTRUMENTS.values() if instrument.enabled],
    }


@router.get("/instruments/{symbol}", response_model=ForexInstrumentResponse)
async def get_instrument(symbol: str) -> Any:
    try:
        return get_forex_instrument(symbol).payload()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _quote_from_cross_rates(symbol: str, cross_rates: dict[str, Any], price: float | None = None, as_of: datetime | None = None) -> ForexQuoteResponse:
    instrument = get_forex_instrument(symbol)
    pair_quotes = cross_rates.get("pair_quotes") or {}
    quote_payload = pair_quotes.get(instrument.symbol) or {}
    if hasattr(quote_payload, "model_dump"):
        quote_payload = quote_payload.model_dump()
    resolved_price = float(price if price is not None else quote_payload.get("rate") or 0)
    provider_symbol = str(instrument.provider_symbol_mappings["yahoo"])
    return ForexQuoteResponse(
        symbol=instrument.symbol,
        display_name=instrument.display_name,
        base_currency=instrument.base_currency,
        quote_currency=instrument.quote_currency,
        price=resolved_price,
        bid=None,
        ask=None,
        spread_pips=None,
        change=None,
        change_percent=None,
        as_of=as_of or cross_rates.get("as_of") or datetime.now(timezone.utc),
        provider="yahoo",
        provider_symbol=provider_symbol,
        delay_status="delayed",
        freshness="latest",
        quality_status="ok" if resolved_price > 0 else "missing",
        price_precision=instrument.price_precision,
        data_source_type=instrument.data_source_type,
        is_proxy=instrument.is_proxy,
        proxy_for=instrument.proxy_for,
    )


@router.get("/quotes", response_model=dict[str, Any])
async def get_quotes() -> dict[str, Any]:
    try:
        cross_rates = await service.get_cross_rates()
        quotes = []
        for symbol in SUPPORTED_FOREX_SYMBOLS:
            if get_forex_instrument(symbol).asset_type == "commodity":
                chart = await service.get_pair_chart(symbol, interval="1d", range_str="1mo")
                quotes.append(_quote_from_cross_rates(symbol, cross_rates, price=float(chart.get("current_rate") or 0), as_of=chart.get("as_of")).model_dump(mode="json"))
            else:
                quotes.append(_quote_from_cross_rates(symbol, cross_rates).model_dump(mode="json"))
        return {"asset_class": "forex", "symbols": list(SUPPORTED_FOREX_SYMBOLS), "quotes": quotes, "as_of": cross_rates.get("as_of")}
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/quotes/{symbol}", response_model=ForexQuoteResponse)
async def get_quote(symbol: str) -> Any:
    try:
        normalized = normalize_forex_symbol(symbol)
        cross_rates = await service.get_cross_rates()
        if get_forex_instrument(normalized).asset_type == "commodity":
            chart = await service.get_pair_chart(normalized, interval="1d", range_str="1mo")
            return _quote_from_cross_rates(normalized, cross_rates, price=float(chart.get("current_rate") or 0), as_of=chart.get("as_of"))
        return _quote_from_cross_rates(normalized, cross_rates)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/candles/{symbol}", response_model=PairChartResponse)
async def get_candles(
    symbol: str,
    interval: str = Query(default="1h"),
    range: str = Query(default="3mo"),
    limit: int = Query(default=500, ge=30, le=5000),
) -> Any:
    try:
        normalized = normalize_forex_symbol(symbol)
        payload = await service.get_pair_chart(normalized, interval=interval, range_str=range)
        payload["candles"] = (payload.get("candles") or [])[-limit:]
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/pairs/{pair:path}", response_model=PairChartResponse)
async def get_pair_chart(
    pair: str,
    interval: str = Query(default="1d"),
    range: str = Query(default="3mo"),
) -> Any:
    try:
        return await service.get_pair_chart(pair, interval=interval, range_str=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/central-banks", response_model=CentralBanksResponse)
async def get_central_banks() -> Any:
    try:
        return await service.get_central_banks()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from backend.market_data.models import AssetClass, Instrument, NewsItem, OHLCVBar, OptionsChain, OrderBookSnapshot, Quote


class Capability(StrEnum):
    QUOTE = "quote"
    HISTORICAL_CANDLES = "historical_candles"
    STREAMING_QUOTES = "streaming_quotes"
    MARKET_DEPTH = "market_depth"
    INSTRUMENT_REFERENCE = "instrument_reference"
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    ECONOMIC_DATA = "economic_data"
    OPTIONS_CHAIN = "options_chain"
    OPTIONS_FLOW = "options_flow"
    CORPORATE_ACTIONS = "corporate_actions"


class QuoteRequest(BaseModel):
    symbol: str
    asset_class: AssetClass = AssetClass.UNKNOWN
    provider: str | None = None
    max_staleness_seconds: float | None = None


class HistoricalCandleRequest(BaseModel):
    symbol: str
    timeframe: str
    start: datetime | None = None
    end: datetime | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    venue: str | None = None
    adjustment_mode: str = "provider_default"
    include_incomplete: bool = False
    provider: str | None = None


class MarketDepthRequest(BaseModel):
    symbol: str
    depth: int = Field(default=10, ge=1, le=100)
    asset_class: AssetClass = AssetClass.UNKNOWN
    provider: str | None = None


class InstrumentReferenceRequest(BaseModel):
    query: str
    asset_class: AssetClass = AssetClass.UNKNOWN
    provider: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class NewsRequest(BaseModel):
    symbol: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    provider: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class OptionsChainRequest(BaseModel):
    underlying_symbol: str
    expiry: datetime | None = None
    provider: str | None = None


@runtime_checkable
class QuoteProvider(Protocol):
    async def get_quote(self, request: QuoteRequest) -> Quote: ...


@runtime_checkable
class HistoricalCandleProvider(Protocol):
    async def get_historical_candles(self, request: HistoricalCandleRequest) -> Sequence[OHLCVBar]: ...


@runtime_checkable
class StreamingQuoteProvider(Protocol):
    async def stream_quotes(self, request: QuoteRequest) -> AsyncIterator[Quote]: ...


@runtime_checkable
class MarketDepthProvider(Protocol):
    async def get_market_depth(self, request: MarketDepthRequest) -> OrderBookSnapshot: ...


@runtime_checkable
class InstrumentReferenceProvider(Protocol):
    async def search_instruments(self, request: InstrumentReferenceRequest) -> Sequence[Instrument]: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    async def get_fundamentals(self, symbol: str) -> object: ...


@runtime_checkable
class NewsProvider(Protocol):
    async def get_news(self, request: NewsRequest) -> Sequence[NewsItem]: ...


@runtime_checkable
class EconomicDataProvider(Protocol):
    async def get_series(self, series_id: str, start: datetime | None = None, end: datetime | None = None) -> object: ...


@runtime_checkable
class OptionsChainProvider(Protocol):
    async def get_options_chain(self, request: OptionsChainRequest) -> OptionsChain: ...


@runtime_checkable
class OptionsFlowProvider(Protocol):
    async def get_options_flow(self, symbol: str) -> object: ...


@runtime_checkable
class CorporateActionsProvider(Protocol):
    async def get_corporate_actions(self, symbol: str) -> object: ...

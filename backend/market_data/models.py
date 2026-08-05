from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FOREX = "forex"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    FUTURE = "future"
    OPTION = "option"
    FIXED_INCOME = "fixed_income"
    INDEX = "index"
    ECONOMIC = "economic"
    NEWS = "news"
    UNKNOWN = "unknown"


class DataType(StrEnum):
    QUOTE = "quote"
    TRADE = "trade"
    OHLCV = "ohlcv"
    ORDER_BOOK = "order_book"
    INSTRUMENT = "instrument"
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    ECONOMIC = "economic"
    OPTIONS_CHAIN = "options_chain"
    OPTIONS_FLOW = "options_flow"
    CORPORATE_ACTIONS = "corporate_actions"


class DataStatus(StrEnum):
    REALTIME = "realtime"
    DELAYED = "delayed"
    END_OF_DAY = "end_of_day"
    HISTORICAL = "historical"
    CACHED = "cached"
    FALLBACK = "fallback"
    SIMULATED = "simulated"
    DEMO = "demo"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    PARTIAL = "partial"


class DataOrigin(StrEnum):
    PROVIDER = "provider"
    CACHE = "cache"
    FALLBACK = "fallback"
    SIMULATED = "simulated"
    DEMO = "demo"
    INTERNAL = "internal"


class EntitlementStatus(StrEnum):
    ENTITLED = "entitled"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class ProviderState(StrEnum):
    INSTALLED = "installed"
    CONFIGURED = "configured"
    AUTHENTICATED = "authenticated"
    ENTITLED = "entitled"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DOWN = "down"
    UNKNOWN = "unknown"


class DataAvailability(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class QualityFlag(StrEnum):
    DUPLICATE_BAR = "duplicate_bar"
    MISSING_BAR = "missing_bar"
    OUT_OF_ORDER = "out_of_order"
    INVALID_OHLC = "invalid_ohlc"
    NEGATIVE_PRICE = "negative_price"
    NEGATIVE_VOLUME = "negative_volume"
    ZERO_VOLUME = "zero_volume"
    OUTLIER_PRICE_MOVE = "outlier_price_move"
    STALE_BAR = "stale_bar"
    INCOMPLETE_BAR = "incomplete_bar"
    SESSION_MISMATCH = "session_mismatch"
    TIMEFRAME_MISMATCH = "timeframe_mismatch"
    TIMESTAMP_DRIFT = "timestamp_drift"
    POSSIBLE_CORPORATE_ACTION = "possible_corporate_action"
    TIMEZONE_INCONSISTENT = "timezone_inconsistent"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class _CanonicalModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, arbitrary_types_allowed=True)


class InstrumentIdentifier(_CanonicalModel):
    scheme: str
    value: str
    provider: str | None = None


class Instrument(_CanonicalModel):
    instrument_id: str
    display_symbol: str
    asset_class: AssetClass = AssetClass.UNKNOWN
    venue: str | None = None
    exchange: str | None = None
    currency: str | None = None
    identifiers: list[InstrumentIdentifier] = Field(default_factory=list)
    provider_symbols: dict[str, str] = Field(default_factory=dict)
    contract_type: str | None = None
    expiry: datetime | None = None
    strike: Decimal | None = None
    option_right: str | None = None
    multiplier: Decimal | None = None
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expiry")
    @classmethod
    def _expiry_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None


class ProviderMetadata(_CanonicalModel):
    provider: str
    original_source: str | None = None
    fallback_source: str | None = None
    requested_data_type: DataType | str
    returned_data_type: DataType | str
    transformation_history: list[str] = Field(default_factory=list)
    extension: dict[str, Any] = Field(default_factory=dict)


class DataQualityMetadata(_CanonicalModel):
    status: list[DataStatus] = Field(default_factory=list)
    origin: DataOrigin = DataOrigin.PROVIDER
    entitlement: EntitlementStatus = EntitlementStatus.UNKNOWN
    availability: DataAvailability = DataAvailability.AVAILABLE
    provider_state: ProviderState = ProviderState.UNKNOWN
    source_timestamp: datetime | None = None
    received_timestamp: datetime = Field(default_factory=utc_now)
    delay_seconds: float | None = None
    cache_status: str | None = None
    cache_age_seconds: float | None = None
    is_stale: bool = False
    is_simulated: bool = False
    is_demo: bool = False
    quality_score: float | None = Field(default=None, ge=0, le=1)
    quality_flags: list[QualityFlag | str] = Field(default_factory=list)

    @field_validator("source_timestamp", "received_timestamp")
    @classmethod
    def _timestamps_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @model_validator(mode="after")
    def _derive_status_flags(self) -> "DataQualityMetadata":
        statuses = {str(status) for status in self.status}
        if self.origin == DataOrigin.CACHE:
            statuses.add(DataStatus.CACHED.value)
        if self.origin == DataOrigin.FALLBACK:
            statuses.add(DataStatus.FALLBACK.value)
        if self.is_stale:
            statuses.add(DataStatus.STALE.value)
        if self.is_simulated:
            statuses.add(DataStatus.SIMULATED.value)
        if self.is_demo:
            statuses.add(DataStatus.DEMO.value)
        self.status = [DataStatus(status) if status in DataStatus._value2member_map_ else status for status in sorted(statuses)]
        return self


class Quote(_CanonicalModel):
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    size: Decimal | None = None
    provider: ProviderMetadata
    quality: DataQualityMetadata


class Trade(_CanonicalModel):
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    price: Decimal
    quantity: Decimal | None = None
    trade_timestamp: datetime
    sequence: str | int | None = None
    provider: ProviderMetadata
    quality: DataQualityMetadata

    @field_validator("trade_timestamp")
    @classmethod
    def _trade_ts_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class OHLCVBar(_CanonicalModel):
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    trade_count: int | None = None
    vwap: Decimal | None = None
    is_complete: bool = True
    provider: ProviderMetadata
    quality: DataQualityMetadata

    @field_validator("open_time", "close_time")
    @classmethod
    def _bar_ts_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _validate_times(self) -> "OHLCVBar":
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        return self


class OrderBookLevel(_CanonicalModel):
    price: Decimal
    quantity: Decimal
    order_count: int | None = None


class OrderBookSnapshot(_CanonicalModel):
    symbol: str
    instrument_id: str | None = None
    asset_class: AssetClass = AssetClass.UNKNOWN
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    sequence: str | int | None = None
    provider: ProviderMetadata
    quality: DataQualityMetadata


class MarketSession(_CanonicalModel):
    venue: str
    session_name: str
    open_time: datetime
    close_time: datetime
    is_open: bool
    is_regular: bool = True


class CorporateAction(_CanonicalModel):
    symbol: str
    action_type: str
    effective_time: datetime
    ratio: Decimal | None = None
    amount: Decimal | None = None
    provider: ProviderMetadata
    quality: DataQualityMetadata


class FundamentalSnapshot(_CanonicalModel):
    symbol: str
    as_of: datetime
    fields: dict[str, Any]
    provider: ProviderMetadata
    quality: DataQualityMetadata


class EconomicObservation(_CanonicalModel):
    series_id: str
    observed_at: datetime
    value: Decimal | None
    publication_time: datetime | None = None
    provider: ProviderMetadata
    quality: DataQualityMetadata


class NewsItem(_CanonicalModel):
    id: str
    headline: str
    published_at: datetime
    source: str
    url: str | None = None
    symbols: list[str] = Field(default_factory=list)
    provider: ProviderMetadata
    quality: DataQualityMetadata


class OptionsContract(_CanonicalModel):
    instrument: Instrument
    underlying_symbol: str
    expiry: datetime
    strike: Decimal
    right: str


class OptionsChain(_CanonicalModel):
    underlying_symbol: str
    as_of: datetime
    contracts: list[OptionsContract] = Field(default_factory=list)
    provider: ProviderMetadata
    quality: DataQualityMetadata

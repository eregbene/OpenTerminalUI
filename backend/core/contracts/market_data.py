from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel


class DataFreshness(StrEnum):
    REAL_TIME = "real_time"
    DELAYED = "delayed"
    END_OF_DAY = "end_of_day"
    HISTORICAL = "historical"
    CACHED = "cached"
    SIMULATED = "simulated"
    FALLBACK = "fallback"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class DataEntitlement(StrEnum):
    AVAILABLE = "available"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class ProviderHealth(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class ProviderStatus(BaseModel):
    provider: str
    health: ProviderHealth = ProviderHealth.UNKNOWN
    entitlement: DataEntitlement = DataEntitlement.UNKNOWN
    error_code: str | None = None
    message: str | None = None


class MarketDataStatus(BaseModel):
    provider: str
    data_type: str
    symbol: str | None = None
    asset_class: str | None = None
    timestamp: datetime | None = None
    age_seconds: float | None = None
    delay_seconds: float | None = None
    source: str | None = None
    fallback_source: str | None = None
    freshness: DataFreshness = DataFreshness.UNAVAILABLE
    entitlement: DataEntitlement = DataEntitlement.UNKNOWN
    health: ProviderHealth = ProviderHealth.UNKNOWN
    error_code: str | None = None

    @classmethod
    def from_quote(
        cls,
        quote: dict,
        *,
        provider: str,
        symbol: str,
        asset_class: str,
        source: str | None = None,
    ) -> "MarketDataStatus":
        raw_ts = quote.get("ts") or quote.get("timestamp")
        timestamp = None
        if isinstance(raw_ts, datetime):
            timestamp = raw_ts.astimezone(timezone.utc)
        elif isinstance(raw_ts, str):
            try:
                timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                timestamp = None
        age = (datetime.now(timezone.utc) - timestamp).total_seconds() if timestamp else None
        freshness = DataFreshness.REAL_TIME
        if age is not None and age > 900:
            freshness = DataFreshness.STALE
        elif source == "cache":
            freshness = DataFreshness.CACHED
        elif provider == "polling":
            freshness = DataFreshness.DELAYED
        return cls(
            provider=provider,
            data_type="quote",
            symbol=symbol,
            asset_class=asset_class,
            timestamp=timestamp,
            age_seconds=age,
            source=source or provider,
            freshness=freshness,
            entitlement=DataEntitlement.AVAILABLE,
            health=ProviderHealth.OK,
        )

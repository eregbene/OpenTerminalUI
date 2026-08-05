from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_data.models import (
    AssetClass,
    DataOrigin,
    DataQualityMetadata,
    DataStatus,
    EntitlementStatus,
    OHLCVBar,
    ProviderMetadata,
    ProviderState,
    Quote,
)


def provider_meta(provider: str = "fixture", data_type: str = "quote") -> ProviderMetadata:
    return ProviderMetadata(provider=provider, requested_data_type=data_type, returned_data_type=data_type)


def quality(
    *,
    status: list[DataStatus] | None = None,
    origin: DataOrigin = DataOrigin.PROVIDER,
    source_timestamp: datetime | None = None,
) -> DataQualityMetadata:
    return DataQualityMetadata(
        status=status or [DataStatus.HISTORICAL],
        origin=origin,
        entitlement=EntitlementStatus.ENTITLED,
        provider_state=ProviderState.CONFIGURED,
        source_timestamp=source_timestamp,
        received_timestamp=datetime.now(timezone.utc),
        quality_score=1.0,
    )


def successful_quote(symbol: str = "AAPL") -> Quote:
    ts = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    return Quote(
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        price=Decimal("123.45"),
        provider=provider_meta("fixture", "quote"),
        quality=quality(status=[DataStatus.REALTIME], source_timestamp=ts),
    )


def delayed_quote(symbol: str = "AAPL") -> Quote:
    ts = datetime(2026, 1, 2, 15, 15, tzinfo=timezone.utc)
    q = successful_quote(symbol)
    q.quality.status = [DataStatus.DELAYED]
    q.quality.delay_seconds = 900
    q.quality.source_timestamp = ts
    return q


def historical_candles(symbol: str = "AAPL", *, duplicate: bool = False, missing: bool = False) -> list[OHLCVBar]:
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    rows: list[OHLCVBar] = []
    for idx in range(5):
        if missing and idx == 2:
            continue
        open_time = start + timedelta(minutes=idx)
        rows.append(
            OHLCVBar(
                symbol=symbol,
                asset_class=AssetClass.EQUITY,
                timeframe="1m",
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=Decimal("100") + idx,
                high=Decimal("101") + idx,
                low=Decimal("99") + idx,
                close=Decimal("100.5") + idx,
                volume=Decimal("1000"),
                is_complete=True,
                provider=provider_meta("fixture", "ohlcv"),
                quality=quality(status=[DataStatus.HISTORICAL], source_timestamp=open_time),
            )
        )
    if duplicate and rows:
        rows.insert(1, rows[0].model_copy(deep=True))
    return rows

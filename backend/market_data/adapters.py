from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.market_data.capabilities import HistoricalCandleRequest, QuoteRequest
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


def _quality(provider_id: str, *, status: DataStatus, source_timestamp: datetime | None = None, origin: DataOrigin = DataOrigin.PROVIDER) -> DataQualityMetadata:
    return DataQualityMetadata(
        status=[status],
        origin=origin,
        entitlement=EntitlementStatus.ENTITLED,
        provider_state=ProviderState.CONFIGURED,
        source_timestamp=source_timestamp,
        received_timestamp=datetime.now(timezone.utc),
        quality_score=0.95 if origin == DataOrigin.PROVIDER else 0.7,
    )


def normalize_legacy_quote(row: dict[str, Any], *, provider_id: str, request: QuoteRequest, fallback: bool = False) -> Quote:
    price = row.get("price") or row.get("ltp") or row.get("last") or row.get("current_price") or row.get("c")
    if price is None:
        raise ValueError("legacy quote has no price")
    raw_ts = row.get("ts") or row.get("timestamp")
    ts = None
    if isinstance(raw_ts, datetime):
        ts = raw_ts.astimezone(timezone.utc)
    elif isinstance(raw_ts, str):
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            ts = None
    origin = DataOrigin.FALLBACK if fallback else DataOrigin.PROVIDER
    status = DataStatus.FALLBACK if fallback else DataStatus.DELAYED
    return Quote(
        symbol=request.symbol.upper(),
        asset_class=request.asset_class,
        price=Decimal(str(price)),
        bid=Decimal(str(row["bid"])) if row.get("bid") is not None else None,
        ask=Decimal(str(row["ask"])) if row.get("ask") is not None else None,
        provider=ProviderMetadata(
            provider=provider_id,
            original_source=str(row.get("source") or provider_id),
            requested_data_type="quote",
            returned_data_type="quote",
            transformation_history=["legacy_quote_normalized"],
        ),
        quality=_quality(provider_id, status=status, source_timestamp=ts, origin=origin),
    )


def normalize_legacy_ohlcv(rows: list[dict[str, Any]], *, provider_id: str, request: HistoricalCandleRequest, fallback: bool = False) -> list[OHLCVBar]:
    out: list[OHLCVBar] = []
    for row in rows:
        raw_t = row.get("t") or row.get("time") or row.get("timestamp")
        if isinstance(raw_t, (int, float)):
            ts = datetime.fromtimestamp(float(raw_t) / (1000 if raw_t > 10_000_000_000 else 1), tz=timezone.utc)
        elif isinstance(raw_t, str):
            ts = datetime.fromisoformat(raw_t.replace("Z", "+00:00")).astimezone(timezone.utc)
        elif isinstance(raw_t, datetime):
            ts = raw_t.astimezone(timezone.utc)
        else:
            raise ValueError("legacy bar missing timestamp")
        out.append(
            OHLCVBar(
                symbol=request.symbol.upper(),
                asset_class=request.asset_class,
                timeframe=request.timeframe,
                open_time=ts,
                close_time=ts + _timeframe_delta(request.timeframe),
                open=Decimal(str(row.get("o") or row.get("open"))),
                high=Decimal(str(row.get("h") or row.get("high"))),
                low=Decimal(str(row.get("l") or row.get("low"))),
                close=Decimal(str(row.get("c") or row.get("close"))),
                volume=Decimal(str(row.get("v") or row.get("volume") or 0)),
                is_complete=bool(row.get("is_complete", True)),
                provider=ProviderMetadata(
                    provider=provider_id,
                    original_source=str(row.get("source") or provider_id),
                    requested_data_type="ohlcv",
                    returned_data_type="ohlcv",
                    transformation_history=["legacy_ohlcv_normalized"],
                ),
                quality=_quality(provider_id, status=DataStatus.FALLBACK if fallback else DataStatus.HISTORICAL, source_timestamp=ts, origin=DataOrigin.FALLBACK if fallback else DataOrigin.PROVIDER),
            )
        )
    return out


def _timeframe_delta(timeframe: str):
    from backend.market_data.resampling import timeframe_delta

    return timeframe_delta(timeframe)

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_data.models import DataQualityMetadata, DataStatus, OHLCVBar, ProviderMetadata


_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1d": 86400,
}


def timeframe_delta(timeframe: str) -> timedelta:
    key = timeframe.lower()
    if key not in _TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return timedelta(seconds=_TIMEFRAME_SECONDS[key])


def align_time(ts: datetime, timeframe: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = ts.astimezone(timezone.utc)
    seconds = int(timeframe_delta(timeframe).total_seconds())
    epoch = int(utc.timestamp())
    return datetime.fromtimestamp((epoch // seconds) * seconds, tz=timezone.utc)


def resample_bars(
    bars: list[OHLCVBar],
    target_timeframe: str,
    *,
    include_incomplete: bool = False,
    now: datetime | None = None,
) -> list[OHLCVBar]:
    if not bars:
        return []
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    bucketed: dict[datetime, list[OHLCVBar]] = defaultdict(list)
    for bar in sorted(bars, key=lambda item: item.open_time):
        bucketed[align_time(bar.open_time, target_timeframe)].append(bar)

    delta = timeframe_delta(target_timeframe)
    out: list[OHLCVBar] = []
    for bucket_start, rows in sorted(bucketed.items()):
        bucket_close = bucket_start + delta
        is_complete = bucket_close <= current and all(row.is_complete for row in rows)
        if not include_incomplete and not is_complete:
            continue
        volume = sum((row.volume or Decimal("0")) for row in rows)
        trade_count = sum((row.trade_count or 0) for row in rows) or None
        vwap = None
        if volume > 0:
            weighted = sum(((row.vwap or row.close) * (row.volume or Decimal("0"))) for row in rows)
            vwap = weighted / volume
        first = rows[0]
        last = rows[-1]
        provider = ProviderMetadata(
            provider=first.provider.provider,
            original_source=first.provider.original_source,
            fallback_source=first.provider.fallback_source,
            requested_data_type="ohlcv",
            returned_data_type="ohlcv",
            transformation_history=[*first.provider.transformation_history, f"resampled:{first.timeframe}->{target_timeframe}"],
        )
        quality = DataQualityMetadata(
            status=[DataStatus.HISTORICAL],
            origin=first.quality.origin,
            entitlement=first.quality.entitlement,
            provider_state=first.quality.provider_state,
            source_timestamp=last.quality.source_timestamp,
            received_timestamp=max(row.quality.received_timestamp for row in rows),
            quality_flags=sorted({str(flag) for row in rows for flag in row.quality.quality_flags}),
            quality_score=min((row.quality.quality_score for row in rows if row.quality.quality_score is not None), default=None),
        )
        out.append(
            OHLCVBar(
                symbol=first.symbol,
                instrument_id=first.instrument_id,
                asset_class=first.asset_class,
                timeframe=target_timeframe,
                open_time=bucket_start,
                close_time=bucket_close,
                open=first.open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=last.close,
                volume=volume,
                trade_count=trade_count,
                vwap=vwap,
                is_complete=is_complete,
                provider=provider,
                quality=quality,
            )
        )
    return out

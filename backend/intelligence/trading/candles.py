from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
TIMEFRAME_BY_SECONDS = {value: key for key, value in TIMEFRAME_SECONDS.items()}


@dataclass(frozen=True)
class CanonicalCandle:
    symbol: str
    timeframe: str
    open_timestamp: datetime
    close_timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    volume_type: str
    source: str
    provider_timestamp: datetime | None
    is_complete: bool
    is_synthetic: bool
    quality_flags: tuple[str, ...] = ()
    ingestion_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_refs: tuple[str, ...] = ()
    resampling_method: str | None = None
    completeness: float = 1.0

    @property
    def timestamp(self) -> int:
        return int(self.open_timestamp.timestamp())

    def ref(self) -> str:
        return f"{self.symbol}:{self.timeframe}:{self.open_timestamp.isoformat()}"

    def as_chart_row(self) -> dict[str, Any]:
        return {"t": self.timestamp, "o": self.open, "h": self.high, "l": self.low, "c": self.close, "v": self.volume}

    def model_dump(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_timestamp": self.open_timestamp.isoformat(),
            "close_timestamp": self.close_timestamp.isoformat(),
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
            "volume_type": self.volume_type,
            "source": self.source,
            "provider_timestamp": self.provider_timestamp.isoformat() if self.provider_timestamp else None,
            "is_complete": self.is_complete,
            "is_synthetic": self.is_synthetic,
            "quality_flags": list(self.quality_flags),
            "ingestion_timestamp": self.ingestion_timestamp.isoformat(),
            "parent_refs": list(self.parent_refs),
            "resampling_method": self.resampling_method,
            "completeness": self.completeness,
        }


def canonicalize_candles(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    source: str,
    now: datetime | None = None,
    volume_type: str | None = None,
) -> tuple[list[CanonicalCandle], dict[str, Any]]:
    interval = TIMEFRAME_SECONDS[timeframe]
    observed: list[CanonicalCandle] = []
    input_timestamps: list[int] = []
    invalid = 0
    duplicates = 0
    now_utc = _utc(now)
    seen: set[int] = set()
    previous_ts = -1
    out_of_order = False
    zero_volume = 0
    inferred_volume_type = volume_type or _infer_volume_type(source)

    for row in rows:
        ts = int(row.get("t") or row.get("timestamp") or 0)
        input_timestamps.append(ts)
        if ts <= previous_ts:
            out_of_order = True
        previous_ts = ts
        if ts in seen:
            duplicates += 1
            continue
        seen.add(ts)
        open_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        close_ts = open_ts + timedelta(seconds=interval)
        flags: list[str] = []
        try:
            open_ = float(row.get("o") if row.get("o") is not None else row.get("open"))
            high = float(row.get("h") if row.get("h") is not None else row.get("high"))
            low = float(row.get("l") if row.get("l") is not None else row.get("low"))
            close = float(row.get("c") if row.get("c") is not None else row.get("close"))
            volume = float(row.get("v") if row.get("v") is not None else row.get("volume") or 0)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if min(open_, high, low, close) <= 0:
            flags.append("NON_POSITIVE_PRICE")
        if high < max(open_, low, close) or low > min(open_, high, close):
            flags.append("INVALID_OHLC")
        if volume <= 0:
            zero_volume += 1
            flags.append("VOLUME_UNAVAILABLE" if inferred_volume_type == "UNAVAILABLE" else "ZERO_VOLUME")
        if "NON_POSITIVE_PRICE" in flags or "INVALID_OHLC" in flags:
            invalid += 1
            continue
        observed.append(
            CanonicalCandle(
                symbol=symbol,
                timeframe=timeframe,
                open_timestamp=open_ts,
                close_timestamp=close_ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                volume_type=inferred_volume_type,
                source=source,
                provider_timestamp=open_ts,
                is_complete=close_ts <= now_utc,
                is_synthetic=False,
                quality_flags=tuple(flags),
            )
        )
    complete = sorted([candle for candle in observed if candle.is_complete], key=lambda candle: candle.open_timestamp)
    return complete, {
        "input_count": len(rows),
        "canonical_count": len(complete),
        "duplicate_candles": duplicates,
        "invalid_ohlc_values": invalid,
        "out_of_order_candles": out_of_order,
        "zero_volume_values": zero_volume,
        "volume_type": inferred_volume_type,
        "excluded_incomplete_candles": len(observed) - len(complete),
        "input_timestamps": input_timestamps,
    }


def audit_canonical_candles(
    candles: list[CanonicalCandle],
    *,
    timeframe: str,
    source: str,
    now: datetime | None = None,
    canonical_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    interval = TIMEFRAME_SECONDS[timeframe]
    rows = sorted(candles, key=lambda candle: candle.open_timestamp)
    meta = canonical_meta or {}
    gap_counts: dict[str, int] = {}
    genuine_missing = 0
    expected_closed = 0
    for prev, cur in zip(rows, rows[1:]):
        missing = max(0, int((cur.timestamp - prev.timestamp) / interval) - 1)
        if not missing:
            continue
        classification = classify_gap(prev.close_timestamp, cur.open_timestamp)
        gap_counts[classification] = gap_counts.get(classification, 0) + missing
        if classification not in {"WEEKEND", "MARKET_CLOSED", "EXPECTED_PROVIDER_BREAK"}:
            genuine_missing += missing
        expected_closed += missing
    expected_closed += len(rows)
    latest_ts = rows[-1].open_timestamp if rows else None
    expected = expected_completed(timeframe, now)
    stale = bool(latest_ts and latest_ts < expected - timedelta(seconds=interval * 2))
    reasons: list[str] = []
    status = "VALID"
    if not rows:
        status = "INVALID"
        reasons.append("REQUIRED_DATA_MISSING")
    if int(meta.get("duplicate_candles") or 0):
        status = "INVALID"
        reasons.append("DUPLICATE_CANDLES")
    if genuine_missing:
        status = "INVALID"
        reasons.append("MISSING_INTERVALS")
    if meta.get("out_of_order_candles"):
        status = "INVALID"
        reasons.append("OUT_OF_ORDER_CANDLES")
    if int(meta.get("invalid_ohlc_values") or 0):
        status = "INVALID"
        reasons.append("INVALID_OHLC")
    if stale and status != "INVALID":
        status = "STALE"
        reasons.append("REQUIRED_DATA_STALE")
    return {
        "status": status,
        "reasons": reasons,
        "source": source,
        "timeframe": timeframe,
        "candle_count": len(rows),
        "latest_completed_timestamp": latest_ts.isoformat() if latest_ts else None,
        "expected_completed_timestamp": expected.isoformat(),
        "duplicate_candles": int(meta.get("duplicate_candles") or 0),
        "missing_intervals": genuine_missing,
        "expected_closed_intervals": expected_closed,
        "gap_classification": gap_counts,
        "out_of_order_candles": bool(meta.get("out_of_order_candles")),
        "zero_volume_values": int(meta.get("zero_volume_values") or 0),
        "volume_type": meta.get("volume_type") or (rows[-1].volume_type if rows else "UNKNOWN"),
        "invalid_ohlc_values": int(meta.get("invalid_ohlc_values") or 0),
        "timezone": "UTC",
        "completed_candles_only": True,
        "canonical": True,
        "source_provenance": {"timeframe": timeframe, "source": source, "method": "direct"},
    }


def resample_candles(candles: list[CanonicalCandle], *, target_timeframe: str, source_timeframe: str = "15m") -> tuple[list[CanonicalCandle], dict[str, Any]]:
    if source_timeframe != "15m" or target_timeframe not in {"1h", "4h"}:
        raise ValueError("Only 15m to 1h/4h resampling is supported")
    factor = TIMEFRAME_SECONDS[target_timeframe] // TIMEFRAME_SECONDS[source_timeframe]
    grouped: dict[int, list[CanonicalCandle]] = {}
    for candle in candles:
        bucket = candle.timestamp // TIMEFRAME_SECONDS[target_timeframe] * TIMEFRAME_SECONDS[target_timeframe]
        grouped.setdefault(bucket, []).append(candle)
    out: list[CanonicalCandle] = []
    incomplete_groups = 0
    for bucket, group in sorted(grouped.items()):
        group = sorted(group, key=lambda candle: candle.open_timestamp)
        expected = [bucket + idx * TIMEFRAME_SECONDS[source_timeframe] for idx in range(factor)]
        observed = [candle.timestamp for candle in group]
        if observed != expected or len(group) != factor or not all(candle.is_complete for candle in group):
            incomplete_groups += 1
            continue
        open_ts = datetime.fromtimestamp(bucket, tz=timezone.utc)
        out.append(
            CanonicalCandle(
                symbol=group[0].symbol,
                timeframe=target_timeframe,
                open_timestamp=open_ts,
                close_timestamp=open_ts + timedelta(seconds=TIMEFRAME_SECONDS[target_timeframe]),
                open=group[0].open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=group[-1].close,
                volume=sum(candle.volume for candle in group),
                volume_type=group[0].volume_type,
                source=group[0].source,
                provider_timestamp=group[-1].provider_timestamp,
                is_complete=True,
                is_synthetic=True,
                parent_refs=tuple(candle.ref() for candle in group),
                resampling_method=f"{factor}x15m_ohlc",
                completeness=1.0,
            )
        )
    return out, {"target_timeframe": target_timeframe, "source_timeframe": source_timeframe, "factor": factor, "complete_groups": len(out), "incomplete_groups": incomplete_groups, "method": f"{factor}x15m_ohlc"}


def classify_gap(prev_close: datetime, cur_open: datetime) -> str:
    if cur_open <= prev_close:
        return "UNKNOWN_GAP"
    probe = prev_close
    weekend_minutes = 0
    maintenance_minutes = 0
    total_minutes = int((cur_open - prev_close).total_seconds() // 60)
    while probe < cur_open:
        if probe.weekday() == 5 or (probe.weekday() == 4 and probe.hour >= 22) or (probe.weekday() == 6 and probe.hour < 22):
            weekend_minutes += 1
        elif probe.hour == 21:
            maintenance_minutes += 1
        probe += timedelta(minutes=1)
    if total_minutes and weekend_minutes / total_minutes >= 0.8:
        return "WEEKEND"
    if total_minutes and maintenance_minutes / total_minutes >= 0.8:
        return "EXPECTED_PROVIDER_BREAK"
    return "PROVIDER_GAP"


def expected_completed(timeframe: str, now: datetime | None = None) -> datetime:
    interval = TIMEFRAME_SECONDS[timeframe]
    now_utc = _utc(now)
    bucket = int(now_utc.timestamp()) // interval * interval
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


def _infer_volume_type(source: str) -> str:
    name = source.lower()
    if "yahoo" in name or "=x" in name:
        return "UNAVAILABLE"
    if "ibkr" in name:
        return "TICK_VOLUME"
    return "QUOTE_COUNT"


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

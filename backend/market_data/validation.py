from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean, pstdev

from backend.market_data.models import OHLCVBar, QualityFlag


@dataclass(frozen=True)
class CandleValidationPolicy:
    expected_interval: timedelta | None = None
    allow_negative_prices: bool = False
    allow_zero_volume: bool = True
    stale_after: timedelta | None = None
    outlier_return_threshold: float = 0.25
    timestamp_drift: timedelta | None = None
    mark_incomplete_latest: bool = True


@dataclass
class CandleValidationResult:
    bars: list[OHLCVBar]
    flags_by_index: dict[int, set[QualityFlag]] = field(default_factory=dict)

    @property
    def all_flags(self) -> set[QualityFlag]:
        out: set[QualityFlag] = set()
        for flags in self.flags_by_index.values():
            out.update(flags)
        return out


def validate_candles(
    bars: list[OHLCVBar],
    *,
    policy: CandleValidationPolicy | None = None,
    now: datetime | None = None,
) -> CandleValidationResult:
    policy = policy or CandleValidationPolicy()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = CandleValidationResult(bars=bars)
    seen: set[datetime] = set()
    closes: list[Decimal] = []

    for idx, bar in enumerate(bars):
        flags = result.flags_by_index.setdefault(idx, set())
        if bar.open_time in seen:
            flags.add(QualityFlag.DUPLICATE_BAR)
        seen.add(bar.open_time)
        if idx > 0 and bar.open_time < bars[idx - 1].open_time:
            flags.add(QualityFlag.OUT_OF_ORDER)
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.high < bar.low:
            flags.add(QualityFlag.INVALID_OHLC)
        if not policy.allow_negative_prices and min(bar.open, bar.high, bar.low, bar.close) < 0:
            flags.add(QualityFlag.NEGATIVE_PRICE)
        if bar.volume is not None and bar.volume < 0:
            flags.add(QualityFlag.NEGATIVE_VOLUME)
        if bar.volume is not None and bar.volume == 0 and not policy.allow_zero_volume:
            flags.add(QualityFlag.ZERO_VOLUME)
        if not bar.is_complete and policy.mark_incomplete_latest:
            flags.add(QualityFlag.INCOMPLETE_BAR)
        if policy.expected_interval:
            actual = bar.close_time - bar.open_time
            if abs(actual.total_seconds() - policy.expected_interval.total_seconds()) > 1:
                flags.add(QualityFlag.TIMEFRAME_MISMATCH)
            if idx > 0:
                spacing = bar.open_time - bars[idx - 1].open_time
                if spacing > policy.expected_interval * 1.5:
                    flags.add(QualityFlag.MISSING_BAR)
                elif spacing < policy.expected_interval:
                    flags.add(QualityFlag.TIMEFRAME_MISMATCH)
        if policy.stale_after and current - bar.close_time > policy.stale_after:
            flags.add(QualityFlag.STALE_BAR)
        if policy.timestamp_drift and bar.quality.source_timestamp:
            if abs((bar.quality.received_timestamp - bar.quality.source_timestamp).total_seconds()) > policy.timestamp_drift.total_seconds():
                flags.add(QualityFlag.TIMESTAMP_DRIFT)
        if closes and closes[-1] != 0:
            ret = abs(float((bar.close - closes[-1]) / closes[-1]))
            if ret >= policy.outlier_return_threshold:
                flags.add(QualityFlag.OUTLIER_PRICE_MOVE)
            if ret >= 0.45:
                flags.add(QualityFlag.POSSIBLE_CORPORATE_ACTION)
        closes.append(bar.close)

    _mark_statistical_outliers(bars, result, policy)
    for idx, flags in result.flags_by_index.items():
        if flags:
            existing = {str(flag) for flag in bars[idx].quality.quality_flags}
            bars[idx].quality.quality_flags = sorted(existing | {flag.value for flag in flags})
            bars[idx].quality.quality_score = max(0.0, 1.0 - 0.1 * len(bars[idx].quality.quality_flags))
    return result


def _mark_statistical_outliers(bars: list[OHLCVBar], result: CandleValidationResult, policy: CandleValidationPolicy) -> None:
    if len(bars) < 4:
        return
    returns: list[float] = []
    for prev, cur in zip(bars, bars[1:]):
        if prev.close == 0:
            returns.append(0.0)
        else:
            returns.append(float((cur.close - prev.close) / prev.close))
    sd = pstdev(returns) if len(returns) > 1 else 0.0
    if sd == 0:
        return
    avg = mean(returns)
    for idx, value in enumerate(returns, start=1):
        if abs(value - avg) / sd >= 5:
            result.flags_by_index.setdefault(idx, set()).add(QualityFlag.OUTLIER_PRICE_MOVE)

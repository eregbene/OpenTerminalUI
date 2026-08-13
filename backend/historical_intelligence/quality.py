"""Data-quality checks for historical ingestion.

Deliberately SEPARATE from backend/brokers/mt5/persistence.py::_candle_quality() -- that
function backs the LIVE M5 trading cycle and must never change behavior as part of this work.
This module's checks are specific to bulk historical backfill, where much richer validation is
both affordable (not on the trading hot path) and necessary (broker demo-server history can
include synthetic/placeholder data years before an instrument existed -- see
providers/mt5_provider.py's docstring for the confirmed 1971 EURUSD example).

Every check here is a FLAG, not a silent rewrite: a bar's `quality` classification and
`lineage.quality_flags` are set from these checks, but the raw OHLC values a provider actually
returned are never altered. "Historical intelligence must not use FAILED/UNTRUSTED data" is
enforced by callers filtering on `quality`, not by this module editing prices.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.historical_intelligence.providers.base import HistoricalBar

VALID = "VALID"
SUSPECT = "SUSPECT"
INVALID = "INVALID"

# A bar is considered FINALIZED (stable enough that a later differing re-fetch is no longer
# trusted -- see ingestion.py's write path) once this much wall-clock time has passed since it
# closed. Roughly 2-3x the bar's own period -- generous enough to absorb the broker's own
# post-close settling window (ticks/spread/volume can still adjust for a short while after a bar
# nominally closes) without leaving a bar "provisional" indefinitely. A bar younger than this may
# still legitimately change on re-fetch; ingestion applies the new value AND records the
# revision. A bar older than this is protected: a differing re-fetch is recorded as a
# post_finalization_anomaly but never applied to mt5_canonical_candles.
FINALIZATION_WINDOWS = {
    "M5": timedelta(minutes=30),
    "M15": timedelta(minutes=45),
    "H1": timedelta(hours=3),
    "H4": timedelta(hours=12),
}


def is_finalized(bar_timestamp_utc: datetime, timeframe: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    bar_timestamp_utc = bar_timestamp_utc if bar_timestamp_utc.tzinfo else bar_timestamp_utc.replace(tzinfo=timezone.utc)
    window = FINALIZATION_WINDOWS.get(timeframe.upper(), timedelta(hours=1))
    return (now - bar_timestamp_utc) >= window

# Forex trades roughly continuously Sun ~22:00 UTC through Fri ~22:00 UTC. A bar timestamped
# deep inside the weekend closure (not just a few minutes either side of the boundary, which can
# be a legitimate broker-server-time artifact) is suspicious.
_WEEKEND_CORE_START_WEEKDAY = 5  # Saturday
_WEEKEND_CORE_END_HOUR_SUNDAY_UTC = 21  # bars before ~21:00 UTC Sunday are still "in the closure"

# A single-bar move larger than this fraction of the bar's own open price, with no matching
# move in neighboring bars, is flagged (not rejected) as an unexplained jump -- thresholds are
# deliberately generous (real gaps happen around weekends/news) since this is a FLAG for later
# review, not a rejection rule.
_EXTREME_JUMP_FRACTION = 0.08
_STALE_RUN_LENGTH = 8  # this many consecutive bars with identical OHLC is flagged as suspicious


def classify_bar(bar: HistoricalBar, *, prior_close: float | None, now: datetime) -> tuple[str, list[str]]:
    """Per-bar classification against ITS OWN values and the immediately preceding bar's close
    only -- run-level checks (stale runs, weekend clustering) are separate, batch-level
    functions below, since they need more than one neighbor."""
    flags: list[str] = []
    bar_time = bar.time if bar.time.tzinfo else bar.time.replace(tzinfo=timezone.utc)

    if bar_time > now + timedelta(minutes=5):
        flags.append("INVALID_FUTURE_TIMESTAMP")
    if bar_time.year < 1990:
        flags.append("INVALID_IMPLAUSIBLE_DATE")

    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(p <= 0 for p in prices):
        flags.append("INVALID_NON_POSITIVE_PRICE")
    elif bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
        flags.append("INVALID_OHLC_INCONSISTENT")

    if bar_time.weekday() == _WEEKEND_CORE_START_WEEKDAY:
        flags.append("SUSPECT_WEEKEND_TIMESTAMP")
    elif bar_time.weekday() == 6 and bar_time.hour < _WEEKEND_CORE_END_HOUR_SUNDAY_UTC:
        flags.append("SUSPECT_WEEKEND_TIMESTAMP")

    if prior_close and prior_close > 0 and "INVALID_NON_POSITIVE_PRICE" not in flags:
        move = abs(bar.close - prior_close) / prior_close
        if move > _EXTREME_JUMP_FRACTION:
            flags.append("SUSPECT_EXTREME_JUMP")

    if any(f.startswith("INVALID") for f in flags):
        return INVALID, flags
    if flags:
        return SUSPECT, flags
    return VALID, flags


def classify_batch(bars: list[HistoricalBar], *, now: datetime | None = None) -> dict[datetime, tuple[str, list[str]]]:
    """Per-bar classification for a whole fetched batch, PLUS the batch-level stale-run check
    (identical OHLC repeated _STALE_RUN_LENGTH+ times in a row -- a common symptom of a
    dead/no-liquidity feed or a provider serving cached/placeholder data). Returns
    {bar.time: (classification, flags)}; `bars` must already be sorted ascending by time."""
    now = now or datetime.now(timezone.utc)
    results: dict[datetime, tuple[str, list[str]]] = {}
    prior_close: float | None = None
    run_key: tuple[float, float, float, float] | None = None
    run_length = 0
    run_members: list[datetime] = []

    def _flush_run() -> None:
        if run_length >= _STALE_RUN_LENGTH:
            for t in run_members:
                classification, flags = results[t]
                if "SUSPECT_STALE_RUN" not in flags:
                    flags = flags + ["SUSPECT_STALE_RUN"]
                    results[t] = (SUSPECT if classification == VALID else classification, flags)

    for bar in bars:
        classification, flags = classify_bar(bar, prior_close=prior_close, now=now)
        results[bar.time] = (classification, flags)
        prior_close = bar.close if "INVALID_NON_POSITIVE_PRICE" not in flags else prior_close

        key = (bar.open, bar.high, bar.low, bar.close)
        if key == run_key:
            run_length += 1
            run_members.append(bar.time)
        else:
            _flush_run()
            run_key = key
            run_length = 1
            run_members = [bar.time]
    _flush_run()
    return results


def compare_provider_overlap(
    left: list[HistoricalBar], left_name: str,
    right: list[HistoricalBar], right_name: str,
) -> dict[str, Any]:
    """Statistical comparison of two providers' bars over their OVERLAPPING timestamps only.
    Deliberately does not require identical prices (different providers/spreads/feeds
    legitimately disagree by small amounts) -- reports the disagreement distribution so a
    caller can judge "close enough to be the same market" vs "obviously incompatible", per the
    explicit instruction not to demand exact equality."""
    left_by_time = {b.time: b for b in left}
    right_by_time = {b.time: b for b in right}
    common_times = sorted(set(left_by_time) & set(right_by_time))
    if not common_times:
        return {
            "left_provider": left_name, "right_provider": right_name,
            "overlapping_bars": 0, "status": "NO_OVERLAP",
        }
    diffs = []
    for t in common_times:
        l, r = left_by_time[t], right_by_time[t]
        if l.close <= 0 or r.close <= 0:
            continue
        diffs.append(abs(l.close - r.close) / ((l.close + r.close) / 2.0))
    if not diffs:
        return {
            "left_provider": left_name, "right_provider": right_name,
            "overlapping_bars": len(common_times), "status": "NO_COMPARABLE_PRICES",
        }
    median_diff = statistics.median(diffs)
    max_diff = max(diffs)
    mean_diff = statistics.fmean(diffs)
    # Thresholds are deliberately generous -- FX/CFD quotes from different providers/liquidity
    # pools routinely differ by tens of pips; this is a coarse "same market or not" sanity check,
    # not a precision reconciliation.
    if median_diff > 0.02 or max_diff > 0.15:
        status = "INCOMPATIBLE"
    elif median_diff > 0.005:
        status = "DIVERGENT"
    else:
        status = "CONSISTENT"
    return {
        "left_provider": left_name, "right_provider": right_name,
        "overlapping_bars": len(common_times),
        "median_relative_diff": round(median_diff, 6),
        "mean_relative_diff": round(mean_diff, 6),
        "max_relative_diff": round(max_diff, 6),
        "status": status,
    }

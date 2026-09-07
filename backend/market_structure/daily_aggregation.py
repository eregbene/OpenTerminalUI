"""Synthesizes Daily (D1) OHLC bars from an already-fetched H4 bar series.

BSI Daily Bias Audit (2026-09-02, `BSI_DAILY_BIAS_AUDIT.md`) Section 5, Option B -- chosen over
native MT5 D1 ingestion because the live, MT5-sourced canonical candle table has ZERO D1 rows
today (confirmed by direct query); the only D1 data that exists in this deployment comes from
secondary reference providers (Yahoo/ForexSB) never wired into BSI's own live/replay path, and
native ingestion would require new broker-side work of unknown history depth. Aggregating from H4
(rather than the also-considered M15) reuses data that is BOTH already MT5-native (no new
provider) AND cheap: H4 bars already span deep history (this deployment's own MT5-sourced H4 data
goes back to 2009 per direct query) and only ~6 H4 bars are needed per calendar day, versus ~96
for M15 -- a materially smaller live-fetch and replay-fetch footprint for the same daily-bar
count.

Pure function module, no I/O: this file adds NO new point-in-time-safety logic of its own -- it
inherits whatever safety property its own input H4 rows already have. Fed `bars_as_of()`-sourced
H4 rows (replay) or `redis_layer.cached_candles()`-sourced H4 rows (live), the emitted Daily bars
are exactly as point-in-time-safe as those inputs, no more, no less. Never fetches anything itself.

Day-boundary convention: America/New_York calendar date, keyed off each H4 bar's own `time` (its
own open timestamp). This is deliberately NOT an arbitrary UTC-midnight choice -- it is the ONLY
concrete day-boundary anchor point found anywhere in the mentor's own 40-video course (the 9:30AM
lesson's explicit "New York midnight open" reference), reused here for consistency with that
established convention rather than inventing an unrelated one.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        t = value
    elif isinstance(value, str):
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"unsupported bar time value: {value!r}")
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def aggregate_daily_bars_from_h4(h4_rows: list[dict[str, Any]], *, symbol: str) -> list[dict[str, Any]]:
    """Groups H4 row dicts (MT5Candle.model_dump(mode="json") shape: symbol/timeframe/time/open/
    high/low/close/tick_volume/spread/close_time/...) by America/New_York calendar date (see
    module docstring for why NY specifically) and emits one aggregated OHLC dict per date found,
    ascending by time. Rows within a day are sorted by their own `time` before aggregating, so
    open/close are correct even if the caller's input isn't perfectly pre-sorted.

    The LAST emitted bar may represent a still-forming NY day if the input H4 series ends mid-day
    -- this mirrors the existing "last bar = current, possibly still-forming" convention already
    used everywhere else in this codebase (e.g. ctx.m15_rows[-1] as the current-price reference),
    not a new convention invented here. Callers needing ONLY fully-closed daily bars should drop
    the last element when its date matches "today" in NY time as of their own `at`/now reference.

    Returns [] for empty input (never raises on missing data -- matches every other bar-fetch
    helper's own fail-safe convention)."""
    if not h4_rows:
        return []
    buckets: dict[date, list[dict[str, Any]]] = {}
    for row in h4_rows:
        t = _parse_time(row.get("time") or row.get("open_time") or row.get("timestamp"))
        ny_date = t.astimezone(NY_TZ).date()
        buckets.setdefault(ny_date, []).append(row)

    out: list[dict[str, Any]] = []
    for ny_date in sorted(buckets.keys()):
        rows_for_day = sorted(buckets[ny_date], key=lambda r: _parse_time(r.get("time") or r.get("open_time") or r.get("timestamp")))
        first, last = rows_for_day[0], rows_for_day[-1]
        out.append({
            "symbol": symbol,
            "timeframe": "D1",
            "time": first.get("time"),
            "open": first.get("open"),
            "high": max(float(r["high"]) for r in rows_for_day),
            "low": min(float(r["low"]) for r in rows_for_day),
            "close": last.get("close"),
            "close_time": last.get("close_time") or last.get("time"),
            "tick_volume": sum(int(r.get("tick_volume") or 0) for r in rows_for_day),
            "spread": last.get("spread", 0),
            "real_volume": sum(int(r.get("real_volume") or 0) for r in rows_for_day),
            "source_bar_count": len(rows_for_day),
        })
    return out

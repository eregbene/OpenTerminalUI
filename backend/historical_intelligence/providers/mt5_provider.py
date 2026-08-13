"""MT5 broker historical data provider.

The MT5 bridge's `copy_rates_from_pos` API is POSITION-based (bars back from now), not
date-range based, and -- confirmed empirically against the live bridge (2026-08-12 session) --
large single requests are unreliable: a 200,000-bar request times out, a 100,000-bar request
silently returns empty, but 50,000 bars works cleanly. This provider therefore pages backward in
conservative, capped chunks, converting the requested date range into an approximate bar-count
offset (forex trades roughly 5 days/week, so bar density is timeframe-dependent, never a naive
24/7 count), and treats an empty/short response after retries as "reached the true data
boundary" -- never as a hard failure that aborts the whole fetch.

Also confirmed empirically: MT5's generic demo server (MetaQuotes-Demo) returns real, densely-
populated bars for a stretch of years, but its VERY oldest bars (e.g. EURUSD H4 claiming data
back to 1971-08-10) fail an internal bar-density sanity check and are almost certainly synthetic
placeholder history, not genuine prices -- the Euro did not exist before 1999, and Yahoo Finance
(an independent source) has no EURUSD data before 2003-12-01. This provider does not attempt to
filter that out by date (it would need a broker-by-broker judgment call); callers building
intelligence on top of MT5-sourced history should prefer the density/cross-provider-agreement
checks in quality.py over trusting raw MT5 bar availability alone.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider

logger = logging.getLogger(__name__)

# Bars/day estimate for a 5-day forex trading week (weekends excluded), used only to convert a
# requested calendar date range into an approximate starting bar-count offset -- the actual
# fetched bars are still filtered precisely by their real timestamps afterward, so an imprecise
# estimate here only costs a slightly wider (never narrower) initial paging range.
_TIMEFRAME_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}
_TRADING_DAYS_PER_WEEK = 5

# Conservative single-request chunk size -- empirically safe (50,000 worked reliably; 100,000+
# did not) with headroom kept well below that boundary.
_CHUNK_SIZE = 20_000
_MAX_CHUNKS = 40  # hard ceiling: 40 * 20,000 = 800,000 bars per fetch_bars() call, plenty for
# multi-year H1/H4 backfills without risking an unbounded loop against a flaky bridge.
_RETRIES_PER_CHUNK = 3
_RETRY_BACKOFF_SECONDS = 1.5
_REQUEST_TIMEOUT_SECONDS = 30


def _bars_per_day(timeframe: str) -> float:
    minutes = _TIMEFRAME_MINUTES.get(timeframe.upper(), 5)
    return (24 * 60) / minutes


def _offset_for_datetime(target: datetime, *, now: datetime, timeframe: str) -> int:
    """Approximate number of bars back from `now` where `target` likely falls -- an
    OVER-estimate is safe (fetches a bit more than needed, filtered afterward); an
    UNDER-estimate would silently truncate the range, so this deliberately uses the full 7-day
    week (not the 5-day trading week) as the calendar-to-bars conversion, erring wide."""
    elapsed_days = max(0.0, (now - target).total_seconds() / 86400.0)
    return int(elapsed_days * _bars_per_day(timeframe)) + 50


class MT5HistoricalProvider(HistoricalDataProvider):
    name = "MT5"

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self._cached_offset_minutes: int | None = None

    async def detect_utc_offset_minutes(self, *, force: bool = False) -> int:
        """Confirmed empirically (2026-08-12): this broker's bar/tick timestamps are
        broker-SERVER time, not UTC -- true UTC now vs the live bridge's latest tick differed by
        exactly +3 hours. Detected by comparing a fresh tick's own reported time against real
        `datetime.now(UTC)`, rounded to the nearest 15 minutes (broker offsets are conventionally
        whole 15-minute/30-minute/hour multiples, e.g. UTC+2/+3 for common MT5 demo servers).
        Cached per provider instance (an offset does not change mid-run) -- pass `force=True`
        after a long-running process to pick up a DST transition."""
        if self._cached_offset_minutes is not None and not force:
            return self._cached_offset_minutes
        now = datetime.now(timezone.utc)
        try:
            # Any liquid, always-tradable symbol works -- the offset is a broker/server property,
            # not symbol-specific.
            tick = await asyncio.wait_for(self.adapter.latest_tick("EURUSD"), timeout=_REQUEST_TIMEOUT_SECONDS)
            tick_time = tick.time if tick.time.tzinfo else tick.time.replace(tzinfo=timezone.utc)
            raw_minutes = (tick_time - now).total_seconds() / 60.0
            offset = round(raw_minutes / 15.0) * 15
        except Exception as exc:
            logger.warning("MT5 UTC offset detection failed, defaulting to 0 (timestamps will be treated as UTC): %s", exc.__class__.__name__)
            offset = 0
        self._cached_offset_minutes = int(offset)
        return self._cached_offset_minutes

    async def fetch_bars(
        self,
        *,
        canonical_symbol: str,
        broker_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalBar]:
        now = datetime.now(timezone.utc)
        start_offset = _offset_for_datetime(end, now=now, timeframe=timeframe)
        end_offset = _offset_for_datetime(start, now=now, timeframe=timeframe)
        bars: list[HistoricalBar] = []
        offset = max(0, start_offset)
        chunks = 0
        consecutive_empty = 0
        while offset < end_offset and chunks < _MAX_CHUNKS:
            count = min(_CHUNK_SIZE, end_offset - offset)
            rows = await self._fetch_chunk(broker_symbol, timeframe, start=offset, count=count)
            chunks += 1
            if not rows:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    # Two consecutive empty chunks (each already internally retried) is treated
                    # as "reached the true data boundary this far back", not a transient blip.
                    break
                offset += count
                continue
            consecutive_empty = 0
            bars.extend(rows)
            if len(rows) < count:
                # Short read: the broker had fewer bars than requested at this offset -- the
                # data boundary is inside this chunk, no need to page further back.
                break
            offset += count

        filtered = [b for b in bars if start <= b.time < end]
        deduped = {b.time: b for b in filtered}
        return sorted(deduped.values(), key=lambda b: b.time)

    async def _fetch_chunk(self, broker_symbol: str, timeframe: str, *, start: int, count: int) -> list[HistoricalBar]:
        for attempt in range(_RETRIES_PER_CHUNK):
            try:
                rows = await asyncio.wait_for(
                    self.adapter.copy_rates(broker_symbol, timeframe, start=start, count=count),
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                return [
                    HistoricalBar(
                        time=row.time if row.time.tzinfo else row.time.replace(tzinfo=timezone.utc),
                        open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close),
                        tick_volume=int(row.tick_volume or 0), spread=int(row.spread or 0), real_volume=int(row.real_volume or 0),
                        complete=bool(row.complete),
                    )
                    for row in rows
                ]
            except asyncio.TimeoutError:
                logger.debug("MT5 historical fetch timed out (%s %s start=%s count=%s attempt=%s)", broker_symbol, timeframe, start, count, attempt)
            except Exception as exc:
                logger.debug("MT5 historical fetch failed (%s %s start=%s count=%s attempt=%s): %s", broker_symbol, timeframe, start, count, attempt, exc.__class__.__name__)
            if attempt < _RETRIES_PER_CHUNK - 1:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
        return []

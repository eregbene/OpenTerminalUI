"""ForexSB (Dukascopy-derived) deep-history provider.

Source: https://data.forexsb.com/datafeed/data/dukascopy/{SYMBOL}{PERIOD}.lb.gz -- a static,
pre-built gzip archive per (symbol, native timeframe), NOT the raw per-hour/per-day Dukascopy
`.bi5` feed. Empirically verified (2026-08) against the live host: metadata + 20 binary files
(10 symbols x {M30, M15}) fetched with zero failures and zero rate-limiting, 0.5-0.8s per file --
materially more reliable than downloading Dukascopy's own hourly `.bi5` files directly (which
showed real 503s and dropped connections under the same testing). Each native file is capped at
exactly 200,000 bars (a ForexSB/platform-side limit, not something this provider can widen), so
depth-by-timeframe varies: M30 reaches back to ~2010 (2009-11 for XAUUSD), M15 to ~2018, M5 to
~2023, M1 only a few recent months -- confirmed per-symbol against the live host before this
provider was written.

H1/H4/D1 have NO native ForexSB file -- they are derived here, in-memory, from the M30 series
(see `_derive_higher_timeframe`), matching the reference downloader's own approach
(github.com/yllvar/fx-historical-data) but reimplemented against this codebase's HistoricalBar/
HistoricalDataProvider contract rather than copied wholesale.

This provider is backfill-only, exactly like YahooHistoricalProvider -- never imported by the
live M5 trading cycle, never used by replay.py's live-decision-replay callers (those default to
provider="MT5" and this provider is never passed to them). It exists purely to widen the corpus
that bulk_replay.py/adaptive_backfill.py read from Historical Intelligence's OFFLINE side.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import random
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://data.forexsb.com/datafeed/data/dukascopy"
_INFO_URL = "https://data.forexsb.com/datafeed/info/premium.json.gz"
_EPOCH_SECONDS = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())

_NATIVE_PERIODS = {"M1": 1, "M5": 5, "M15": 15, "M30": 30}
_DERIVED_MINUTES = {"H1": 60, "H4": 240, "D1": 1440}

# Retries/backoff: sized for a static-file CDN-style host (fast, reliable per empirical testing),
# not tuned for Dukascopy's own throttled raw feed -- this is deliberately lighter than that would
# need, but still real, with jitter so a multi-symbol batch run doesn't retry in lockstep.
_RETRIES = 4
_BASE_BACKOFF_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 30


def _parse_records(raw: bytes, *, price_scale: float, volume_scale: int, broker_symbol: str, period_label: str) -> list[HistoricalBar]:
    """Decodes ForexSB's little-endian binary record format (24 bytes: time/open/high/low/close
    int32; 28 bytes: + volume/spread int32) -- see IMPLEMENTATION.md in the reference repo,
    verified directly against real decoded prices before this provider was written (2005-01-04
    EURUSD M1 decoded to real, historically-plausible prices). `time` is minutes since
    2000-01-01 UTC -- already true UTC, no broker-offset correction needed (Dukascopy's own
    archive is UTC-labeled, confirmed via the hour-bucketed raw .bi5 URLs sharing the same
    convention).

    Malformed records (corrupt size, OHLC inconsistency, non-positive price) are DROPPED, never
    silently persisted -- Part 3's explicit requirement. `quality.classify_batch` still runs
    downstream in ingestion.py on whatever this returns as a second, independent check; this is
    the first line of defense against genuinely corrupt binary data."""
    rec_size = 28 if len(raw) % 28 == 0 else (24 if len(raw) % 24 == 0 else 0)
    if rec_size == 0:
        logger.warning("ForexSB provider: corrupt record size for %s %s (raw=%d bytes divides evenly by neither 24 nor 28) -- discarding batch", broker_symbol, period_label, len(raw))
        return []

    bars: list[HistoricalBar] = []
    dropped = 0
    for offset in range(0, len(raw), rec_size):
        try:
            if rec_size == 28:
                t, o, h, l, c, v, s = struct.unpack_from("<iiiiiii", raw, offset)
            else:
                t, o, h, l, c, v = struct.unpack_from("<iiiiii", raw, offset)
                s = 0
        except struct.error:
            dropped += 1
            continue

        open_, high, low, close = o / price_scale, h / price_scale, l / price_scale, c / price_scale
        if min(open_, high, low, close) <= 0:
            dropped += 1
            continue
        if not (low <= open_ <= high and low <= close <= high):
            dropped += 1
            continue

        try:
            ts = datetime.fromtimestamp(_EPOCH_SECONDS + t * 60, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            dropped += 1
            continue
        if ts.year < 1990 or ts > datetime.now(timezone.utc) + timedelta(days=1):
            dropped += 1
            continue

        volume = max(1, v)
        if volume_scale and volume_scale > 1:
            volume = (volume + volume_scale - 1) // volume_scale

        bars.append(HistoricalBar(time=ts, open=open_, high=high, low=low, close=close, tick_volume=volume, spread=max(0, s), real_volume=0, complete=True))

    if dropped:
        logger.info("ForexSB provider: dropped %d/%d malformed records for %s %s", dropped, dropped + len(bars), broker_symbol, period_label)

    seen: dict[datetime, HistoricalBar] = {}
    for b in bars:
        seen[b.time] = b  # last write wins on a duplicate timestamp -- ForexSB files are not expected to have dupes, but never trust silently
    return sorted(seen.values(), key=lambda b: b.time)


def _derive_higher_timeframe(m30_bars: list[HistoricalBar], period_minutes: int) -> list[HistoricalBar]:
    """Deterministic OHLCV resample (Part 5): OPEN=first, HIGH=max, LOW=min, CLOSE=last,
    VOLUME=sum, SPREAD=max of constituents (conservative -- a derived bar's effective spread
    risk is bounded by its widest constituent, never averaged down). UTC-aligned boundaries
    (epoch-second modulo period, valid since the Unix epoch itself is UTC midnight). No
    lookahead: each derived bar is built ONLY from M30 bars whose own timestamp falls inside its
    bucket. Never fabricates a bucket with zero underlying bars -- a weekend/holiday gap in the
    M30 series simply produces no derived candle for that period, never a synthetic flat one, so
    a gap in M30 can never masquerade as a malformed H1/H4/D1 candle downstream."""
    if not m30_bars:
        return []
    period_seconds = period_minutes * 60
    buckets: dict[int, list[HistoricalBar]] = {}
    for b in m30_bars:
        epoch_seconds = int(b.time.timestamp())
        boundary_seconds = epoch_seconds - (epoch_seconds % period_seconds)
        buckets.setdefault(boundary_seconds, []).append(b)

    derived: list[HistoricalBar] = []
    for boundary_seconds in sorted(buckets):
        members = sorted(buckets[boundary_seconds], key=lambda m: m.time)
        boundary = datetime.fromtimestamp(boundary_seconds, tz=timezone.utc)
        derived.append(HistoricalBar(
            time=boundary,
            open=members[0].open, high=max(m.high for m in members), low=min(m.low for m in members), close=members[-1].close,
            tick_volume=sum(m.tick_volume for m in members), spread=max(m.spread for m in members),
            real_volume=sum(m.real_volume for m in members), complete=True,
        ))
    return derived


class ForexSBHistoricalProvider(HistoricalDataProvider):
    name = "FOREXSB"

    def __init__(self) -> None:
        self._metadata: dict[str, Any] | None = None
        self._metadata_lock = asyncio.Lock()
        # Process-local cache of decoded M30 series per broker_symbol -- H1/H4/D1 all derive
        # from the SAME M30 fetch, so a single backfill run asking for all four timeframes for
        # one symbol downloads+decodes the M30 file exactly once, not four times.
        self._native_cache: dict[tuple[str, str], list[HistoricalBar]] = {}

    async def _get_metadata(self) -> dict[str, Any]:
        if self._metadata is not None:
            return self._metadata
        async with self._metadata_lock:
            if self._metadata is None:
                self._metadata = await asyncio.to_thread(self._fetch_metadata_sync)
            return self._metadata

    @staticmethod
    def _fetch_metadata_sync() -> dict[str, Any]:
        req = urllib.request.Request(_INFO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            data = resp.read()
        return json.loads(gzip.decompress(data))

    def is_proxy_for(self, canonical_symbol: str) -> bool:
        # ForexSB's XAUUSD is Dukascopy CFD gold -- the same kind of instrument MT5 brokers quote
        # for XAUUSD (unlike Yahoo's GC=F COMEX futures), so this is NOT an instrument-identity
        # proxy situation. Whether it's statistically trustworthy enough to enter the corpus is a
        # separate, data-quality question -- see quality.compare_provider_overlap, run explicitly
        # against MT5 before treating ForexSB XAUUSD history as trusted (Part 8).
        return False

    async def fetch_bars(
        self,
        *,
        canonical_symbol: str,
        broker_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalBar]:
        tf = timeframe.upper()
        symbol = broker_symbol.upper()
        meta_all = await self._get_metadata()
        info = meta_all.get(symbol) or meta_all.get(canonical_symbol.upper())
        if info is None:
            logger.warning("ForexSB provider: no metadata for %s -- returning empty, not fabricating scale", canonical_symbol)
            return []
        price_scale = float(info.get("priceScale") or 100000)
        volume_scale = int(info.get("volumeScale") or 1)

        if tf in _NATIVE_PERIODS:
            bars = await self._fetch_native(symbol, tf, price_scale, volume_scale)
        elif tf in _DERIVED_MINUTES:
            m30 = await self._fetch_native(symbol, "M30", price_scale, volume_scale)
            bars = _derive_higher_timeframe(m30, _DERIVED_MINUTES[tf])
        else:
            logger.debug("ForexSB provider: unsupported timeframe %s for %s", tf, symbol)
            return []

        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        return [b for b in bars if start <= b.time < end]

    async def _fetch_native(self, broker_symbol: str, tf: str, price_scale: float, volume_scale: int) -> list[HistoricalBar]:
        cache_key = (broker_symbol, tf)
        cached = self._native_cache.get(cache_key)
        if cached is not None:
            return cached
        period_num = _NATIVE_PERIODS[tf]
        raw = await asyncio.to_thread(self._fetch_and_decompress_sync, broker_symbol, period_num)
        bars = _parse_records(raw, price_scale=price_scale, volume_scale=volume_scale, broker_symbol=broker_symbol, period_label=tf) if raw is not None else []
        self._native_cache[cache_key] = bars
        return bars

    @staticmethod
    def _fetch_and_decompress_sync(broker_symbol: str, period_num: int) -> bytes | None:
        url = f"{_BASE_URL}/{broker_symbol}{period_num}.lb.gz"
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
                    compressed = resp.read()
                return gzip.decompress(compressed)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404:
                    logger.info("ForexSB provider: %s not found (404) -- symbol/period unavailable, not retrying", url)
                    return None
            except Exception as exc:
                last_exc = exc
            if attempt < _RETRIES - 1:
                backoff = _BASE_BACKOFF_SECONDS * (2 ** attempt) + random.uniform(0, 1.5)
                logger.info("ForexSB provider: fetch failed for %s (attempt %d/%d): %s -- retrying in %.1fs", url, attempt + 1, _RETRIES, last_exc, backoff)
                time.sleep(backoff)
        logger.warning("ForexSB provider: fetch permanently failed for %s after %d attempts: %s", url, _RETRIES, last_exc)
        return None

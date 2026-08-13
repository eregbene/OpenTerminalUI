"""Incremental, gap-aware historical ingestion (Part 3).

Never re-downloads bars already persisted in mt5_canonical_candles for the SAME (provider,
broker_symbol, timeframe) -- coverage is checked first, and only genuinely missing sub-ranges
("gaps") are fetched. A normal weekend forex closure is not a gap; anything wider is, so a real
broker/bridge outage or a hole in the data actually gets backfilled instead of silently assumed
covered.

Provider-neutral by construction: every function here takes a HistoricalDataProvider and never
branches on provider identity, other than tagging persisted rows with `provider`/`proxy` from
the provider itself (`provider.name`, `provider.is_proxy_for(...)`).
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import quality
from backend.historical_intelligence.orm import HistoricalIngestionRunORM, HistoricalProviderReconciliationORM
from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# A gap is real missing data once the portion of it that ISN'T explained by ordinary forex
# weekend closures (Friday ~22:00 UTC to Sunday ~22:00 UTC, subtracted explicitly below rather
# than folded into one flat threshold -- a flat multi-hour threshold would either miss real
# weekday outages or false-positive on every weekend) exceeds this tolerance. Generous enough to
# absorb broker-server-time skew and minor feed hiccups without triggering a re-fetch storm, but
# small enough that a short backfill request against a symbol with zero existing coverage is
# still correctly recognized as a real gap (a flat multi-hour tolerance would otherwise silently
# swallow any request narrower than itself).
_GAP_TOLERANCE = timedelta(minutes=20)

_TIMEFRAME_DELTAS = {"M5": timedelta(minutes=5), "M15": timedelta(minutes=15), "H1": timedelta(hours=1), "H4": timedelta(hours=4)}


def _weekend_closure_within(start: datetime, end: datetime) -> timedelta:
    """Approximate total forex-weekend-closure time within [start, end) -- every Friday
    22:00 UTC through the following Sunday 22:00 UTC that the range overlaps."""
    closure = timedelta()
    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        if cursor.weekday() == 4:  # Friday
            window_start = max(start, cursor.replace(hour=22))
            window_end = min(end, cursor.replace(hour=22) + timedelta(hours=48))
            if window_end > window_start:
                closure += window_end - window_start
        cursor += timedelta(days=1)
    return closure


def _is_real_gap(gap_start: datetime, gap_end: datetime) -> bool:
    if gap_end <= gap_start:
        return False
    raw = gap_end - gap_start
    remaining = raw - _weekend_closure_within(gap_start, gap_end)
    return remaining > _GAP_TOLERANCE


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_id(provider: str, symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
    raw = f"{provider}:{symbol}:{timeframe}:{start.isoformat()}:{end.isoformat()}:{utcnow().isoformat()}"
    return "HIR_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def existing_coverage(*, provider: str, broker_symbol: str, timeframe: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = (
            db.query(
                func.min(MT5CanonicalCandleORM.timestamp),
                func.max(MT5CanonicalCandleORM.timestamp),
                func.count(MT5CanonicalCandleORM.candle_id),
            )
            .filter(MT5CanonicalCandleORM.provider == provider, MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(), MT5CanonicalCandleORM.timeframe == timeframe.upper())
            .first()
        )
    if row is None or row[2] == 0:
        return None
    return {"earliest": row[0], "latest": row[1], "count": row[2]}


def _existing_timestamps(*, provider: str, broker_symbol: str, timeframe: str, start: datetime, end: datetime) -> list[datetime]:
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM.timestamp)
            .filter(
                MT5CanonicalCandleORM.provider == provider,
                MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(),
                MT5CanonicalCandleORM.timeframe == timeframe.upper(),
                MT5CanonicalCandleORM.timestamp >= start,
                MT5CanonicalCandleORM.timestamp < end,
            )
            .order_by(MT5CanonicalCandleORM.timestamp.asc())
            .all()
        )
    return [r[0] if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc) for r in rows]


def detect_gaps(*, provider: str, broker_symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Returns [{"gap_start", "gap_end"}] sub-ranges of [start, end) with no persisted coverage
    wider than the weekend-tolerant threshold. A bar at timestamp T is treated as covering
    [T, T+timeframe) -- coverage advances PAST a known bar's own timestamp, not just to it, so
    an already-fetched bar is never re-requested. A symbol with NO coverage at all returns a
    single gap spanning the whole requested range (subject to the same weekend tolerance -- a
    short request that happens to fall entirely within a weekend closure correctly yields no
    gap, since no real data should exist there anyway)."""
    delta = _TIMEFRAME_DELTAS.get(timeframe.upper(), timedelta(minutes=15))
    existing = _existing_timestamps(provider=provider, broker_symbol=broker_symbol, timeframe=timeframe, start=start, end=end)
    gaps: list[dict[str, Any]] = []
    cursor = start
    for ts in existing:
        if ts > cursor and _is_real_gap(cursor, ts):
            gaps.append({"gap_start": cursor.isoformat(), "gap_end": ts.isoformat()})
        cursor = max(cursor, ts + delta)
    if end > cursor and _is_real_gap(cursor, end):
        gaps.append({"gap_start": cursor.isoformat(), "gap_end": end.isoformat()})
    return gaps


def _revision_id(provider: str, broker_symbol: str, timeframe: str, bar_timestamp: datetime, revision_number: int) -> str:
    return f"{provider}:{broker_symbol.upper()}:{timeframe.upper()}:{bar_timestamp.isoformat()}:r{revision_number}"


def _persist_bars(bars: list[HistoricalBar], *, provider: HistoricalDataProvider, canonical_symbol: str, broker_symbol: str, timeframe: str, dataset_policy: str, broker_utc_offset_minutes: int = 0) -> dict[str, int]:
    """Writes bars into mt5_canonical_candles (the fast-lookup current-value table) AND
    mt5_candle_revisions (the append-only audit trail). A bar already FINALIZED (see
    quality.is_finalized) whose incoming value differs from its current canonical value is
    PROTECTED -- the canonical row is left untouched, and the differing observation is recorded
    as a post_finalization_anomaly revision rather than silently applied. This is the fix for
    the confirmed overwrite bug (see migration 0052's docstring)."""
    if not bars:
        return {"persisted": 0, "suspect": 0, "invalid": 0, "protected": 0, "revisions_written": 0}
    now = utcnow()
    quality_by_time = quality.classify_batch(bars, now=now)
    proxy = provider.is_proxy_for(canonical_symbol)
    offset = timedelta(minutes=broker_utc_offset_minutes)
    persisted = suspect = invalid = protected = revisions_written = 0

    from backend.historical_intelligence.orm import MT5CandleRevisionORM

    with SessionLocal() as db:
        bar_times = [b.time for b in bars]
        # Dict keys are normalized to tz-aware UTC explicitly rather than trusted as returned by
        # the DB driver -- SQLite's DateTime(timezone=True) columns round-trip as NAIVE datetimes
        # (confirmed: this caused a real lookup-miss bug under the sqlite-backed test suite,
        # silently duplicating revision_number=1 instead of writing revision 2). Postgres
        # preserves tzinfo correctly, but normalizing here removes the dependency on that
        # driver-specific behavior entirely.
        existing_canonical = {
            (row.timestamp if row.timestamp.tzinfo else row.timestamp.replace(tzinfo=timezone.utc)): row
            for row in db.query(MT5CanonicalCandleORM).filter(
                MT5CanonicalCandleORM.provider == provider.name.upper(),
                MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(),
                MT5CanonicalCandleORM.timeframe == timeframe.upper(),
                MT5CanonicalCandleORM.timestamp.in_(bar_times),
            )
        }
        latest_revision_by_time: dict[datetime, MT5CandleRevisionORM] = {}
        for rev in db.query(MT5CandleRevisionORM).filter(
            MT5CandleRevisionORM.provider == provider.name.upper(),
            MT5CandleRevisionORM.broker_symbol == broker_symbol.upper(),
            MT5CandleRevisionORM.timeframe == timeframe.upper(),
            MT5CandleRevisionORM.bar_timestamp.in_(bar_times),
        ).order_by(MT5CandleRevisionORM.revision_number.asc()):
            rev_time = rev.bar_timestamp if rev.bar_timestamp.tzinfo else rev.bar_timestamp.replace(tzinfo=timezone.utc)
            latest_revision_by_time[rev_time] = rev  # last write in ascending order wins -> latest revision

        for bar in bars:
            classification, flags = quality_by_time.get(bar.time, (quality.VALID, []))
            bar_timestamp_utc = bar.time - offset
            existing_row = existing_canonical.get(bar.time)
            latest_rev = latest_revision_by_time.get(bar.time)
            unchanged = latest_rev is not None and (latest_rev.open, latest_rev.high, latest_rev.low, latest_rev.close) == (bar.open, bar.high, bar.low, bar.close)
            # Finalization is ALWAYS recomputed against the current write's `now` -- never read
            # from a flag stored by an earlier write. A bar observed 2 minutes after it closed is
            # correctly not-yet-finalized at that moment (finalized=False is stored on that
            # revision/canonical row), but a LATER write re-observing the same bar hours after it
            # closed must still protect it: the bar has since become finalized in wall-clock
            # time, regardless of what was true when the earlier row was written.
            will_be_finalized = quality.is_finalized(bar_timestamp_utc, timeframe, now=now)

            if will_be_finalized and existing_row is not None and not unchanged and (existing_row.open, existing_row.high, existing_row.low, existing_row.close) != (bar.open, bar.high, bar.low, bar.close):
                # Protected: a finalized bar's canonical value is never overwritten. Record the
                # differing observation as an audit-only anomaly revision.
                next_rev_number = (latest_rev.revision_number + 1) if latest_rev else 1
                db.add(MT5CandleRevisionORM(
                    revision_id=_revision_id(provider.name.upper(), broker_symbol, timeframe, bar.time, next_rev_number),
                    provider=provider.name.upper(), canonical_symbol=canonical_symbol.upper(), broker_symbol=broker_symbol.upper(), timeframe=timeframe.upper(),
                    bar_timestamp=bar.time, bar_timestamp_utc=bar_timestamp_utc, broker_utc_offset_minutes=broker_utc_offset_minutes,
                    observed_at=now, revision_number=next_rev_number,
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                    tick_volume=bar.tick_volume, spread=bar.spread, real_volume=bar.real_volume,
                    finalized=True, post_finalization_anomaly=True, created_at=now,
                ))
                protected += 1
                revisions_written += 1
                continue

            if not unchanged:
                next_rev_number = (latest_rev.revision_number + 1) if latest_rev else 1
                db.add(MT5CandleRevisionORM(
                    revision_id=_revision_id(provider.name.upper(), broker_symbol, timeframe, bar.time, next_rev_number),
                    provider=provider.name.upper(), canonical_symbol=canonical_symbol.upper(), broker_symbol=broker_symbol.upper(), timeframe=timeframe.upper(),
                    bar_timestamp=bar.time, bar_timestamp_utc=bar_timestamp_utc, broker_utc_offset_minutes=broker_utc_offset_minutes,
                    observed_at=now, revision_number=next_rev_number,
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                    tick_volume=bar.tick_volume, spread=bar.spread, real_volume=bar.real_volume,
                    finalized=will_be_finalized, post_finalization_anomaly=False, created_at=now,
                ))
                revisions_written += 1

            candle_id = f"{provider.name.upper()}:{broker_symbol.upper()}:{timeframe.upper()}:{bar.time.isoformat()}"
            row = existing_row or MT5CanonicalCandleORM(candle_id=candle_id)
            row.provider = provider.name.upper()
            row.dataset_policy = dataset_policy
            row.canonical_symbol = canonical_symbol.upper()
            row.broker_symbol = broker_symbol.upper()
            row.timeframe = timeframe.upper()
            row.timestamp = bar.time
            row.timestamp_utc = bar_timestamp_utc
            row.broker_utc_offset_minutes = broker_utc_offset_minutes
            row.finalized = will_be_finalized
            row.open, row.high, row.low, row.close = bar.open, bar.high, bar.low, bar.close
            row.tick_volume = bar.tick_volume
            row.spread = bar.spread
            row.real_volume = bar.real_volume
            row.quality = classification
            row.delayed = False
            row.proxy = proxy
            row.lineage = {
                "provider": provider.name.upper(),
                "dataset_policy": dataset_policy,
                "source": f"historical_intelligence.ingestion:{provider.name}",
                "symbol_mapping": {"canonical": canonical_symbol.upper(), "provider_symbol": provider.provider_symbol(canonical_symbol), "broker": broker_symbol.upper()},
                "proxy": proxy,
                "quality_flags": flags,
                "broker_utc_offset_minutes": broker_utc_offset_minutes,
            }
            row.fetched_at = now
            row.updated_at = now
            db.merge(row)
            persisted += 1
            if classification == quality.SUSPECT:
                suspect += 1
            elif classification == quality.INVALID:
                invalid += 1
        db.commit()
    return {"persisted": persisted, "suspect": suspect, "invalid": invalid, "protected": protected, "revisions_written": revisions_written}


async def backfill(
    provider: HistoricalDataProvider,
    *,
    canonical_symbol: str,
    broker_symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime | None = None,
    dataset_policy: str | None = None,
    broker_utc_offset_minutes: int | None = None,
) -> dict[str, Any]:
    """Incremental backfill for one (provider, canonical_symbol, timeframe). Only fetches the
    gaps between `start` and `end` that aren't already covered -- safe to call repeatedly
    (e.g. on a schedule) without re-downloading existing history.

    `broker_utc_offset_minutes`: if omitted, auto-detected via `provider.detect_utc_offset_minutes()`
    when the provider exposes that method (MT5HistoricalProvider does; providers whose
    timestamps are already true UTC, e.g. Yahoo, simply don't define it and default to 0)."""
    end = end or utcnow()
    dataset_policy = dataset_policy or f"{provider.name.upper()}_BACKFILL"
    run_id = _run_id(provider.name, canonical_symbol, timeframe, start, end)
    t0 = time.perf_counter()

    if broker_utc_offset_minutes is None:
        detector = getattr(provider, "detect_utc_offset_minutes", None)
        broker_utc_offset_minutes = await detector() if detector else 0

    gaps = detect_gaps(provider=provider.name.upper(), broker_symbol=broker_symbol, timeframe=timeframe, start=start, end=end)
    with SessionLocal() as db:
        run = HistoricalIngestionRunORM(
            run_id=run_id, provider=provider.name.upper(), canonical_symbol=canonical_symbol.upper(),
            broker_symbol=broker_symbol.upper(), timeframe=timeframe.upper(),
            requested_start=start, requested_end=end, gaps_detected=gaps, status="RUNNING", started_at=utcnow(),
        )
        db.add(run)
        db.commit()

    if not gaps:
        with SessionLocal() as db:
            run = db.get(HistoricalIngestionRunORM, run_id)
            run.status = "COMPLETED"
            run.completed_at = utcnow()
            run.duration_ms = (time.perf_counter() - t0) * 1000
            db.commit()
        return {"run_id": run_id, "status": "COMPLETED", "gaps": [], "bars_fetched": 0, "bars_persisted": 0}

    total_fetched = total_persisted = total_suspect = total_invalid = total_protected = 0
    error: str | None = None
    for gap in gaps:
        gap_start = datetime.fromisoformat(gap["gap_start"])
        gap_end = datetime.fromisoformat(gap["gap_end"])
        try:
            bars = await provider.fetch_bars(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe=timeframe, start=gap_start, end=gap_end)
        except Exception as exc:
            logger.warning("Historical ingestion: fetch failed for %s %s %s [%s..%s]: %s", provider.name, broker_symbol, timeframe, gap_start, gap_end, exc.__class__.__name__)
            error = f"{exc.__class__.__name__} during gap [{gap_start.isoformat()}..{gap_end.isoformat()}]"
            continue
        total_fetched += len(bars)
        outcome = _persist_bars(bars, provider=provider, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe=timeframe, dataset_policy=dataset_policy, broker_utc_offset_minutes=broker_utc_offset_minutes)
        total_persisted += outcome["persisted"]
        total_suspect += outcome["suspect"]
        total_invalid += outcome["invalid"]
        total_protected += outcome["protected"]

    status = "COMPLETED" if error is None else ("PARTIAL" if total_persisted > 0 else "FAILED")
    with SessionLocal() as db:
        run = db.get(HistoricalIngestionRunORM, run_id)
        run.bars_fetched = total_fetched
        run.bars_persisted = total_persisted
        run.bars_flagged_suspect = total_suspect
        run.bars_flagged_invalid = total_invalid
        run.status = status
        run.error = error
        run.completed_at = utcnow()
        run.duration_ms = (time.perf_counter() - t0) * 1000
        db.commit()

    return {
        "run_id": run_id, "status": status, "gaps": gaps,
        "bars_fetched": total_fetched, "bars_persisted": total_persisted,
        "bars_flagged_suspect": total_suspect, "bars_flagged_invalid": total_invalid,
        "bars_protected": total_protected, "broker_utc_offset_minutes": broker_utc_offset_minutes, "error": error,
    }


def _bars_from_canonical(*, provider: str, broker_symbol: str, timeframe: str, start: datetime, end: datetime) -> list[HistoricalBar]:
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM)
            .filter(
                MT5CanonicalCandleORM.provider == provider,
                MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(),
                MT5CanonicalCandleORM.timeframe == timeframe.upper(),
                MT5CanonicalCandleORM.timestamp >= start,
                MT5CanonicalCandleORM.timestamp < end,
            )
            .order_by(MT5CanonicalCandleORM.timestamp.asc())
            .all()
        )
    return [
        HistoricalBar(time=r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc), open=r.open, high=r.high, low=r.low, close=r.close, tick_volume=r.tick_volume, spread=r.spread, real_volume=r.real_volume)
        for r in rows
    ]


def reconcile_providers(*, canonical_symbol: str, timeframe: str, start: datetime, end: datetime, left_provider: str = "MT5", right_provider: str = "YAHOO", left_broker_symbol: str | None = None, right_broker_symbol: str | None = None) -> dict[str, Any]:
    """Part 4: statistical comparison of two already-ingested providers over their overlapping
    coverage. Reads only from mt5_canonical_candles -- does not fetch anything itself, so it's
    safe/cheap to call anytime after both providers have been backfilled for the given range."""
    left_bars = _bars_from_canonical(provider=left_provider.upper(), broker_symbol=left_broker_symbol or canonical_symbol, timeframe=timeframe, start=start, end=end)
    right_bars = _bars_from_canonical(provider=right_provider.upper(), broker_symbol=right_broker_symbol or canonical_symbol, timeframe=timeframe, start=start, end=end)
    report = quality.compare_provider_overlap(left_bars, left_provider.upper(), right_bars, right_provider.upper())
    reconciliation_id = _run_id(f"{left_provider}-vs-{right_provider}", canonical_symbol, timeframe, start, end)
    with SessionLocal() as db:
        row = HistoricalProviderReconciliationORM(
            reconciliation_id=reconciliation_id, canonical_symbol=canonical_symbol.upper(), timeframe=timeframe.upper(),
            left_provider=left_provider.upper(), right_provider=right_provider.upper(),
            overlapping_bars=report.get("overlapping_bars", 0),
            median_relative_diff=report.get("median_relative_diff"), mean_relative_diff=report.get("mean_relative_diff"), max_relative_diff=report.get("max_relative_diff"),
            status=report["status"], created_at=utcnow(),
        )
        db.add(row)
        db.commit()
    return {"reconciliation_id": reconciliation_id, **report}

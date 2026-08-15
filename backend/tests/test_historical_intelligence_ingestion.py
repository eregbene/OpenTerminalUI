"""Phase 1 regression tests: provider-neutral ingestion, dedup, incremental/gap-aware backfill,
data-quality classification, and provider reconciliation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import ingestion, quality
from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider
from backend.shared.db import Base


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingestion, "SessionLocal", SessionLocal)
    return SessionLocal


def _bar(t: datetime, close: float = 1.1000, *, high: float | None = None, low: float | None = None) -> HistoricalBar:
    return HistoricalBar(time=t, open=close, high=high if high is not None else close + 0.0005, low=low if low is not None else close - 0.0005, close=close, tick_volume=100)


class FakeProvider(HistoricalDataProvider):
    """Deterministic, no-network provider for tests -- returns one M15 bar every 15 minutes
    across whatever [start, end) range it's asked for, tracking every call so tests can assert
    incremental behavior (only the actual gap gets fetched, not the whole requested range)."""

    def __init__(self, name: str = "FAKE", *, proxy_symbols: set[str] | None = None) -> None:
        self.name = name
        self._proxy_symbols = proxy_symbols or set()
        self.calls: list[tuple[datetime, datetime]] = []

    def is_proxy_for(self, canonical_symbol: str) -> bool:
        return canonical_symbol.upper() in self._proxy_symbols

    async def fetch_bars(self, *, canonical_symbol, broker_symbol, timeframe, start, end) -> list[HistoricalBar]:
        self.calls.append((start, end))
        bars = []
        t = start
        while t < end:
            bars.append(_bar(t, close=1.1000 + (t.minute / 10000.0)))
            t += timedelta(minutes=15)
        return bars


def test_backfill_persists_and_dedupes(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    provider = FakeProvider()
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)

    result1 = asyncio.run(ingestion.backfill(provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=end))
    assert result1["status"] == "COMPLETED"
    assert result1["bars_persisted"] == 16  # 4 hours / 15 min

    with SessionLocal() as db:
        count = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.provider == "FAKE").count()
    assert count == 16

    # Re-running the SAME backfill (identical bars re-fetched) must upsert, not duplicate.
    result2 = asyncio.run(ingestion.backfill(provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=end))
    assert result2["status"] == "COMPLETED"
    with SessionLocal() as db:
        count = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.provider == "FAKE").count()
    assert count == 16


def test_backfill_is_incremental_only_fetches_missing_gap(monkeypatch):
    _session_factory(monkeypatch)
    provider = FakeProvider()
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    middle = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)

    asyncio.run(ingestion.backfill(provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=middle))
    assert provider.calls == [(start, middle)]

    # Second call covers the WIDER [start, end) range -- only the new [middle, end) gap should
    # actually be fetched, not the whole range again.
    asyncio.run(ingestion.backfill(provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=end))
    assert provider.calls[-1] == (middle, end)
    assert len(provider.calls) == 2


def test_detect_gaps_ignores_normal_weekend_closure(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    # Friday 22:00 UTC close, Sunday 22:00 UTC reopen -- exactly the modeled closure window, so
    # the "real" (non-weekend) remainder is ~0 regardless of how small the tolerance is.
    friday_close = datetime(2026, 1, 2, 22, 0, tzinfo=timezone.utc)  # 2026-01-02 is a Friday
    sunday_open = friday_close + timedelta(hours=48)
    with SessionLocal() as db:
        for t in (friday_close, sunday_open):
            db.add(MT5CanonicalCandleORM(candle_id=f"FAKE:EURUSD:M15:{t.isoformat()}", provider="FAKE", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", timestamp=t, open=1.1, high=1.1, low=1.1, close=1.1))
        db.commit()

    gaps = ingestion.detect_gaps(provider="FAKE", broker_symbol="EURUSD", timeframe="M15", start=friday_close, end=sunday_open + timedelta(minutes=15))
    assert gaps == []


def test_detect_gaps_flags_real_outage(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # 2026-01-05 is a Monday
    gap_start = start + timedelta(minutes=15)  # contiguous with `start`'s own M15 bar -- no
    # confounding sub-gap between them
    resumed = gap_start + timedelta(hours=5)  # 5h gap on a weekday -- not a weekend closure
    end = resumed + timedelta(minutes=15)  # exactly covers resumed's own bar -- no trailing gap
    with SessionLocal() as db:
        for t in (start, gap_start, resumed):
            db.add(MT5CanonicalCandleORM(candle_id=f"FAKE:EURUSD:M15:{t.isoformat()}", provider="FAKE", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", timestamp=t, open=1.1, high=1.1, low=1.1, close=1.1))
        db.commit()

    gaps = ingestion.detect_gaps(provider="FAKE", broker_symbol="EURUSD", timeframe="M15", start=start, end=end)
    assert len(gaps) == 1
    # The known bar AT gap_start covers [gap_start, gap_start+15m) -- the real missing region
    # starts right after that bar's own coverage ends, not at gap_start itself.
    assert gaps[0]["gap_start"] == (gap_start + timedelta(minutes=15)).isoformat()
    assert gaps[0]["gap_end"] == resumed.isoformat()


def test_quality_flags_future_timestamp():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    bar = _bar(now + timedelta(days=1))
    classification, flags = quality.classify_bar(bar, prior_close=None, now=now)
    assert classification == quality.INVALID
    assert "INVALID_FUTURE_TIMESTAMP" in flags


def test_quality_flags_non_positive_price():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    bar = HistoricalBar(time=now - timedelta(hours=1), open=0.0, high=0.0, low=0.0, close=0.0)
    classification, flags = quality.classify_bar(bar, prior_close=1.1, now=now)
    assert classification == quality.INVALID
    assert "INVALID_NON_POSITIVE_PRICE" in flags


def test_quality_flags_extreme_jump():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    bar = _bar(now - timedelta(hours=1), close=1.5000)  # >8% jump from prior_close=1.1000
    classification, flags = quality.classify_bar(bar, prior_close=1.1000, now=now)
    assert classification == quality.SUSPECT
    assert "SUSPECT_EXTREME_JUMP" in flags


def test_quality_flags_weekend_timestamp():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    saturday = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)  # a Saturday
    bar = _bar(saturday)
    classification, flags = quality.classify_bar(bar, prior_close=None, now=now)
    assert "SUSPECT_WEEKEND_TIMESTAMP" in flags


def test_quality_batch_flags_stale_run():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    base = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    bars = [_bar(base + timedelta(minutes=15 * i), close=1.1000) for i in range(10)]  # identical OHLC x10
    results = quality.classify_batch(bars, now=now)
    flagged = [flags for (_status, flags) in results.values() if "SUSPECT_STALE_RUN" in flags]
    assert len(flagged) >= quality._STALE_RUN_LENGTH


def test_provider_neutral_same_table_different_provider_column(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    mt5_provider = FakeProvider(name="MT5")
    yahoo_provider = FakeProvider(name="YAHOO", proxy_symbols={"XAUUSD"})
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc)

    asyncio.run(ingestion.backfill(mt5_provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=end))
    asyncio.run(ingestion.backfill(yahoo_provider, canonical_symbol="EURUSD", broker_symbol="EURUSD=X", timeframe="M15", start=start, end=end))

    with SessionLocal() as db:
        providers = {row.provider for row in db.query(MT5CanonicalCandleORM).all()}
    assert providers == {"MT5", "YAHOO"}


def test_xauusd_yahoo_marked_proxy_not_silently_identical(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    yahoo_provider = FakeProvider(name="YAHOO", proxy_symbols={"XAUUSD"})
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc)

    asyncio.run(ingestion.backfill(yahoo_provider, canonical_symbol="XAUUSD", broker_symbol="GC=F", timeframe="M15", start=start, end=end))

    with SessionLocal() as db:
        rows = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.canonical_symbol == "XAUUSD").all()
    assert rows
    assert all(row.proxy is True for row in rows)
    assert all(row.lineage.get("proxy") is True for row in rows)


def test_reconcile_providers_flags_incompatible(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(MT5CanonicalCandleORM(candle_id="MT5:EURUSD:H1:a", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="H1", timestamp=t, open=1.10, high=1.10, low=1.10, close=1.10))
        db.add(MT5CanonicalCandleORM(candle_id="YAHOO:EURUSD:H1:a", provider="YAHOO", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="H1", timestamp=t, open=1.50, high=1.50, low=1.50, close=1.50))
        db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", SessionLocal)

    report = ingestion.reconcile_providers(canonical_symbol="EURUSD", timeframe="H1", start=t - timedelta(minutes=1), end=t + timedelta(minutes=1), left_broker_symbol="EURUSD", right_broker_symbol="EURUSD")
    assert report["status"] == "INCOMPATIBLE"
    assert report["overlapping_bars"] == 1


def test_reconcile_providers_accepts_small_divergence(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(MT5CanonicalCandleORM(candle_id="MT5:EURUSD:H1:a", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="H1", timestamp=t, open=1.1000, high=1.1000, low=1.1000, close=1.1000))
        db.add(MT5CanonicalCandleORM(candle_id="YAHOO:EURUSD:H1:a", provider="YAHOO", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="H1", timestamp=t, open=1.1002, high=1.1002, low=1.1002, close=1.1002))
        db.commit()
    monkeypatch.setattr(ingestion, "SessionLocal", SessionLocal)

    report = ingestion.reconcile_providers(canonical_symbol="EURUSD", timeframe="H1", start=t - timedelta(minutes=1), end=t + timedelta(minutes=1), left_broker_symbol="EURUSD", right_broker_symbol="EURUSD")
    assert report["status"] == "CONSISTENT"


def test_persist_bars_chunked_prefetch_handles_batch_larger_than_chunk_size(monkeypatch):
    """Regression test for the ForexSB integration's real Postgres failure: a single fetch_bars()
    response large enough to exceed the 65,535-bound-parameter limit on an IN(...) prefetch. Uses
    a monkeypatched tiny _IN_CLAUSE_CHUNK_SIZE so the multi-chunk code path is actually exercised
    without needing tens of thousands of real rows in a unit test."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(ingestion, "_IN_CLAUSE_CHUNK_SIZE", 3)
    provider = FakeProvider()
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15 * 10)  # 10 bars, spanning 4 chunks of size 3

    result = asyncio.run(ingestion.backfill(provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", start=start, end=end))
    assert result["bars_persisted"] == 10

    with SessionLocal() as db:
        rows = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.provider == "FAKE").all()
        assert len(rows) == 10
        assert len(set(r.timestamp for r in rows)) == 10  # no duplicate/collapsed timestamps across chunk boundaries


def test_persist_bars_mixed_new_and_existing_rows_in_one_batch(monkeypatch):
    """The brand-new-row fast path (bulk Core INSERT) and the existing-row slow path (db.merge)
    must coexist correctly within a single _persist_bars call -- a real scenario whenever a
    backfill's requested range partially overlaps already-ingested data. Calls _persist_bars
    directly (bypassing backfill()'s gap detection, which would otherwise simply exclude the
    already-covered timestamp from the fetch range and never exercise the mixed-batch path at
    all) with one bar matching a pre-existing, NOT-yet-finalized row (so it takes the update path,
    not the separate protected-anomaly path) and four genuinely new bars."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(ingestion, "SessionLocal", SessionLocal)
    now = datetime.now(timezone.utc)
    t_existing = now - timedelta(minutes=5)  # recent -- within M15's 45-minute finalization window, so still updatable
    with SessionLocal() as db:
        db.add(MT5CanonicalCandleORM(candle_id="FAKE:EURUSD:M15:old", provider="FAKE", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", timestamp=t_existing, open=1.0, high=1.0, low=1.0, close=1.0, finalized=False))
        db.commit()

    provider = FakeProvider()
    bars = [_bar(t_existing, close=1.2345)] + [_bar(t_existing + timedelta(minutes=15 * i), close=1.3000 + i * 0.001) for i in range(1, 5)]
    outcome = ingestion._persist_bars(bars, provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")
    assert outcome["persisted"] == 5
    assert outcome["protected"] == 0

    with SessionLocal() as db:
        all_rows = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.provider == "FAKE").order_by(MT5CanonicalCandleORM.timestamp.asc()).all()
        assert len(all_rows) == 5
        closes = [r.close for r in all_rows]
        # The pre-existing (not finalized) row was UPDATED via the merge path -- new close applied
        # (never left at the old fixture value of 1.0), and the four brand-new bars' closes are
        # all present too (order-independent check, robust to SQLite's DateTime round-trip
        # dropping tzinfo/microsecond precision on the primary key column).
        assert abs(min(closes, key=lambda c: abs(c - 1.2345)) - 1.2345) < 1e-9
        assert 1.0 not in [round(c, 4) for c in closes]
        for i in range(1, 5):
            expected = 1.3000 + i * 0.001
            assert abs(min(closes, key=lambda c: abs(c - expected)) - expected) < 1e-9

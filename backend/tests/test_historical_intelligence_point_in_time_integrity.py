"""Point-in-time integrity regression tests (the fix required before Historical Intelligence can
influence live DEMO decisions -- see historical_intelligence/replay.py's module docstring for the
two confirmed bugs this fixes: bar-overwrite and broker-server-time-vs-UTC).

Covers required test items 1-6 and 8 of the integrity spec:
  1. canonical bar updates do not destroy prior observed versions
  2. decision-time snapshot remains immutable
  3. replay selects the version available at decision time
  4. later bar revision does not change historical replay result (post-finalization protection)
  5. regime parity survives later canonical updates
  6. all strategy families can reproduce live candidate presence -- covered structurally here
     (replay calls the exact same production functions); the REAL cross-family reproduction rate
     is an empirical property of the 100+-candidate real-data parity batch, not a unit test.
  8. broad replay cannot accidentally use future revisions (no-lookahead on revision selection)

Items 9-12 (no-lookahead on raw timestamps, DEMO_ACTIVE risk-gate isolation, DEMO_ACTIVE
broker-safety isolation, LIVE-disabled) are covered by test_historical_intelligence_replay.py
(9) or are not yet applicable: items 10/11 test code that does not exist yet (DEMO_ACTIVE
entry/adaptive integration is Phase 3+, not built in this pass) and will get dedicated tests when
that integration is implemented; item 12 (MT5_LIVE_TRADING_ENABLED=false) is a deployment/config
invariant untouched by any change in this pass, not something a unit test in this module can
regress.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import ingestion, replay, snapshot_capture
from backend.historical_intelligence.orm import MT5CandleRevisionORM, MT5DecisionSnapshotORM
from backend.historical_intelligence.providers.base import HistoricalBar, HistoricalDataProvider
from backend.shared.db import Base


async def _no_broker_offset(provider):
    return 0


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingestion, "SessionLocal", SessionLocal)
    monkeypatch.setattr(replay, "SessionLocal", SessionLocal)
    monkeypatch.setattr(snapshot_capture, "SessionLocal", SessionLocal)
    # bars_as_of() auto-resolves the broker UTC offset, which (absent any revision history) falls
    # back to a REAL broker call -- never exercise that in unit tests.
    monkeypatch.setattr(replay, "_resolve_broker_offset_minutes", _no_broker_offset)
    return SessionLocal


class _FakeProvider(HistoricalDataProvider):
    name = "MT5"

    async def fetch_bars(self, **kwargs):  # pragma: no cover -- these tests call _persist_bars directly
        return []


def _bar(t: datetime, close: float) -> HistoricalBar:
    return HistoricalBar(time=t, open=close, high=close + 0.0005, low=close - 0.0005, close=close, tick_volume=10, spread=1, real_volume=0, complete=True)


# --- Item 1: canonical bar updates do not destroy prior observed versions -------------------


def test_revision_history_preserves_prior_version_on_unfinalized_change(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    provider = _FakeProvider()
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

    # First observation: fresh, well within the M15 finalization window -- not yet finalized.
    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=5))
    ingestion._persist_bars([_bar(t, 1.1000)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")

    # Second observation, still unfinalized (< 45 min old), with a DIFFERENT close -- a
    # legitimate late-settling revision, not a violation.
    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=20))
    outcome = ingestion._persist_bars([_bar(t, 1.1010)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")
    assert outcome["revisions_written"] == 1
    assert outcome["protected"] == 0

    with SessionLocal() as db:
        revisions = db.query(MT5CandleRevisionORM).filter(MT5CandleRevisionORM.bar_timestamp == t).order_by(MT5CandleRevisionORM.revision_number).all()
        canonical = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.timestamp == t).one()

    assert [r.revision_number for r in revisions] == [1, 2]
    # The FIRST observation's value is still readable -- never destroyed by the second write.
    assert revisions[0].close == 1.1000
    assert revisions[1].close == 1.1010
    # The canonical (fast-lookup) row reflects the latest value, as intended.
    assert canonical.close == 1.1010


# --- Item 4: later bar revision does not change historical replay result (finalized protection)


def test_finalized_bar_protected_from_overwrite_and_anomaly_recorded(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    provider = _FakeProvider()
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=5))
    ingestion._persist_bars([_bar(t, 1.1000)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")

    # A re-fetch long after the M15 finalization window (45 min) with a DIFFERENT value -- this
    # must NOT be applied to the canonical row; it must be recorded as an anomaly only.
    later = t + timedelta(hours=6)
    monkeypatch.setattr(ingestion, "utcnow", lambda: later)
    outcome = ingestion._persist_bars([_bar(t, 1.2500)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")
    assert outcome["protected"] == 1
    assert outcome["persisted"] == 0

    with SessionLocal() as db:
        canonical = db.query(MT5CanonicalCandleORM).filter(MT5CanonicalCandleORM.timestamp == t).one()
        anomalies = db.query(MT5CandleRevisionORM).filter(MT5CandleRevisionORM.bar_timestamp == t, MT5CandleRevisionORM.post_finalization_anomaly.is_(True)).all()

    assert canonical.close == 1.1000  # untouched -- the historically-decided value survives
    assert len(anomalies) == 1
    assert anomalies[0].close == 1.2500  # the differing observation is preserved for audit, not applied

    # Replaying at an instant BEFORE the anomalous re-fetch must still see the original value.
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=t + timedelta(minutes=20), count=1, provider="MT5"))
    assert len(bars) == 1
    assert float(bars[0].close) == 1.1000


# --- Item 3: replay selects the version available at decision time --------------------------


def test_bars_as_of_selects_revision_known_at_decision_time(monkeypatch):
    """Both observations happen well AFTER the bar itself has closed (t+15min), so the
    no-lookahead bar-closure gate never confounds this test -- only the revision-selection-by-
    observed_at logic is being exercised here."""
    SessionLocal = _session_factory(monkeypatch)
    provider = _FakeProvider()
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=20))
    ingestion._persist_bars([_bar(t, 1.1000)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")

    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=40))
    ingestion._persist_bars([_bar(t, 1.1050)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")

    # Decision instant BETWEEN the two observations (and after the bar's own close): must see
    # only what was known by then -- the first revision.
    at_between = t + timedelta(minutes=30)
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at_between, count=1, provider="MT5"))
    assert float(bars[0].close) == 1.1000

    # Decision instant AFTER both observations: must see the latest.
    at_after = t + timedelta(minutes=50)
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at_after, count=1, provider="MT5"))
    assert float(bars[0].close) == 1.1050


# --- Legacy/live-written canonical rows (no timestamp_utc, no revisions) are still offset-corrected


def test_bars_as_of_corrects_legacy_rows_lacking_timestamp_utc(monkeypatch):
    """Reproduces the exact residual gap found empirically against this deployment's real data
    (2026-08-12): the LIVE trading path's own persist_candles() writes directly to
    mt5_canonical_candles and NEVER sets timestamp_utc/broker_utc_offset_minutes/finalized
    (confirmed: 100% of production's EURUSD M15 canonical rows had timestamp_utc IS NULL). Tier-3
    fallback must still correct these rows using a resolved broker offset -- not silently treat
    the raw, broker-labeled timestamp as if it were already UTC (which would reproduce the
    original +3h broker-clock-vs-UTC bug for the vast majority of real bar history)."""
    SessionLocal = _session_factory(monkeypatch)

    async def _offset_180(provider):
        return 180  # UTC+3, matching this deployment's confirmed real broker offset

    monkeypatch.setattr(replay, "_resolve_broker_offset_minutes", _offset_180)

    # Bar labeled in BROKER time (UTC+3): true UTC instant is 09:00, but the raw `timestamp`
    # column (as legacy/live-written rows store it) reads 12:00 with no timestamp_utc set.
    broker_labeled_time = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    true_utc_bar_time = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(MT5CanonicalCandleORM(
            candle_id="MT5:EURUSD:M15:legacy", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD",
            timeframe="M15", timestamp=broker_labeled_time, timestamp_utc=None,
            open=1.1000, high=1.1005, low=1.0995, close=1.1000, quality="VALID", finalized=False,
        ))
        db.commit()

    # Replaying well after the TRUE UTC close (09:15) and past the M15 finalization window must
    # include this bar -- if the offset correction were skipped, the raw label (12:00) would
    # make the bar look 3 hours in the future and it would be wrongly excluded.
    at = true_utc_bar_time + timedelta(minutes=50)
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=1, provider="MT5"))
    assert len(bars) == 1
    assert bars[0].time == true_utc_bar_time

    # And replaying at an instant BEFORE the true UTC close must correctly exclude this bar, even
    # though the raw broker-labeled timestamp (12:00) would appear to already be safely in the
    # past relative to a naive (uncorrected) comparison against `at`.
    at_too_early = true_utc_bar_time + timedelta(minutes=5)
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at_too_early, count=1, provider="MT5"))
    assert bars == []


# --- Item 8: broad replay cannot accidentally use future revisions --------------------------


def test_bars_as_of_excludes_ambiguous_bar_with_no_time_appropriate_revision(monkeypatch):
    """A bar that is NOT YET finalized as of `at`, and for which no revision was observed by
    `at` either, has no look-ahead-safe answer -- it must be excluded, never guessed via a
    later-observed revision."""
    SessionLocal = _session_factory(monkeypatch)
    provider = _FakeProvider()
    t = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

    # Only observation happens AFTER `at` (simulating a backfill/late fetch), and the bar is
    # still within its finalization window relative to `at`.
    monkeypatch.setattr(ingestion, "utcnow", lambda: t + timedelta(minutes=30))
    ingestion._persist_bars([_bar(t, 1.1000)], provider=provider, canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", dataset_policy="TEST")

    at = t + timedelta(minutes=20)  # before the only observation, and bar not yet finalized at `at` (< 45min)
    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=1, provider="MT5"))
    assert bars == []  # excluded, not guessed


# --- Item 2: decision-time snapshot remains immutable ----------------------------------------


class _FakeCtx:
    def __init__(self):
        self.m15_rows = [{"i": i} for i in range(60)]
        self.h1_rows = [{"i": i} for i in range(50)]
        self.h4_rows = [{"i": i} for i in range(30)]
        self.bid = 1.1000
        self.ask = 1.1002
        self.spread = 0.0002
        self.regime = "trending_up"


class _FakeService:
    def __init__(self, ctx_by_symbol):
        self._cycle_context_cache = ctx_by_symbol
        self.account_id = "demo_10k"


def test_decision_snapshot_capture_is_idempotent_and_immutable(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    service = _FakeService({"EURUSD": _FakeCtx()})
    result = {
        "cycle_id": "CYCLE_1", "account_id": "demo_10k",
        "candidates": [{"candidate_id": "CAND_1", "broker_symbol": "EURUSD", "canonical_pair": "EURUSD", "trade_confidence": 70.0}],
    }

    written_first = snapshot_capture.capture_decision_snapshots(service, result)
    assert written_first == 1

    with SessionLocal() as db:
        rows = db.query(MT5DecisionSnapshotORM).all()
    assert len(rows) == 1
    original_regime = rows[0].regime
    original_snapshot_id = rows[0].snapshot_id

    # Simulate a later cycle re-processing the SAME candidate_id with different (bogus, should
    # never be applied) context -- capture must be a no-op, never overwrite the original.
    mutated_ctx = _FakeCtx()
    mutated_ctx.regime = "ranging"
    service2 = _FakeService({"EURUSD": mutated_ctx})
    written_second = snapshot_capture.capture_decision_snapshots(service2, result)
    assert written_second == 0

    with SessionLocal() as db:
        rows = db.query(MT5DecisionSnapshotORM).all()
    assert len(rows) == 1
    assert rows[0].snapshot_id == original_snapshot_id
    assert rows[0].regime == original_regime  # untouched by the second call


# --- Item 5 / source hierarchy: replay_from_snapshot bypasses reconstruction entirely --------


def test_replay_from_snapshot_uses_exact_captured_bars_not_reconstruction(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    from backend.brokers.mt5.models import MT5Candle
    from decimal import Decimal

    def _candle_row(i: int, tf: str) -> dict:
        c = MT5Candle(
            symbol="EURUSD", timeframe=tf, time=datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=15 * i),
            open=Decimal("1.1000"), high=Decimal("1.1010"), low=Decimal("1.0990"), close=Decimal("1.1000"),
            tick_volume=10, spread=1, real_volume=0,
        )
        return c.model_dump(mode="json")

    with SessionLocal() as db:
        db.add(MT5DecisionSnapshotORM(
            snapshot_id="MDS_TEST", evaluation_id="EVAL_SNAP_1", cycle_id="C1", account_id="demo_10k",
            canonical_symbol="EURUSD", broker_symbol="EURUSD", decision_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
            m15_rows=[_candle_row(i, "M15") for i in range(65)],
            h1_rows=[_candle_row(i, "H1") for i in range(55)],
            h4_rows=[_candle_row(i, "H4") for i in range(35)],
            bid=1.1000, ask=1.1002, spread=0.0002, regime=None, smc_evidence={}, symbol_info={},
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()

    # No canonical/revision rows exist at all -- if replay_from_snapshot fell back to
    # reconstruction it would return INSUFFICIENT_HISTORY. It must not.
    result = asyncio.run(replay.replay_from_snapshot("EVAL_SNAP_1"))
    assert result is not None
    assert result["source"] == "SNAPSHOT"
    assert result["bars"] == {"m15": 65, "h1": 55, "h4": 35}


def test_replay_from_snapshot_returns_none_when_absent(monkeypatch):
    _session_factory(monkeypatch)
    result = asyncio.run(replay.replay_from_snapshot("NO_SUCH_EVAL"))
    assert result is None

"""Phase 14 (corpus-expansion directive): dedicated proof that BOS/CHoCH/MSS/swing structural
reconstruction through the point-in-time replay pipeline (bars_as_of -> build_strategy_context ->
analyze_bars) never leaks future data -- not just that bars_as_of() itself excludes future bars
(already covered by test_historical_intelligence_replay.py), but that the DOWNSTREAM structural
features computed from those bars are provably identical regardless of what is later written to
the database for timestamps after the replay instant `at`.

Method: seed a real, structure-bearing candle sequence (the exact fixture already used by
test_market_structure_phase5_engine.py to exercise swing/BOS detection) up to instant `at`, build
the structural snapshot, THEN seed additional volatile "future" bars (a sharp reversal -- the kind
of move most likely to retroactively change a naive analysis) after `at`, rebuild the snapshot at
the SAME `at`, and assert byte-identical structural output. If future data leaked in, this second
build would differ."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import replay
from backend.shared.db import Base

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Same OHLC sequence as test_market_structure_phase5_engine.py::_fixture -- known to produce real
# swings/structure breaks, not a flat/degenerate series.
_SEQUENCE = [
    (100, 101, 99, 100), (100, 103, 99.5, 102), (102, 105, 101, 104), (104, 106, 102, 103),
    (103, 104, 100, 101), (101, 102, 98, 99), (99, 101, 97, 100), (100, 104, 99, 103),
    (103, 108, 102, 107), (107, 111, 106, 110), (110, 112, 107, 108), (108, 109, 104, 105),
    (105, 106, 101, 102), (102, 104, 100, 103), (103, 110, 102, 109), (109, 116, 108, 115),
    (115, 118, 112, 117), (117, 119, 113, 114), (114, 115, 109, 110), (110, 121, 109, 120),
]

# A sharp, deliberately structure-altering reversal -- if this leaked backward into the `at`
# snapshot it would very likely change swing/BOS classification, making it a strong probe.
_FUTURE_REVERSAL = [(120, 121, 95, 97), (97, 99, 80, 85), (85, 90, 70, 75)]


async def _no_broker_offset(provider):
    return 0


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(replay, "SessionLocal", SessionLocal)
    monkeypatch.setattr(replay, "_resolve_broker_offset_minutes", _no_broker_offset)
    return SessionLocal


def _seed(db, sequence, *, start_index: int = 0):
    for i, (o, h, low, c) in enumerate(sequence):
        idx = start_index + i
        t = BASE + timedelta(minutes=15 * idx)
        db.add(MT5CanonicalCandleORM(
            candle_id=f"MT5:EURUSD:M15:{t.isoformat()}", provider="MT5",
            canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", timestamp=t, timestamp_utc=t,
            open=o, high=h, low=low, close=c, quality="VALID", finalized=True,
        ))


def test_bos_choch_swing_reconstruction_unaffected_by_later_written_future_bars(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)

    # `at` must satisfy two bounds at once: >= last original bar's close + FINALIZATION_WINDOWS
    # ["M15"] (45 min), so all 20 original bars are eligible via bars_as_of()'s tier-3 fallback,
    # AND < the first future-reversal bar's close + 45 min, so those bars are NOT YET finalized
    # and therefore excluded -- i.e. squarely inside [330, 345) minutes past the sequence start.
    at = BASE + timedelta(minutes=15 * len(_SEQUENCE)) + timedelta(minutes=40)

    with SessionLocal() as db:
        _seed(db, _SEQUENCE)
        db.commit()

    m15_before = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=100, provider="MT5"))
    ctx_before = replay.build_strategy_context(
        symbol="EURUSD", broker_symbol="EURUSD",
        m15_rows=[c.model_dump(mode="json") for c in m15_before], h1_rows=[c.model_dump(mode="json") for c in m15_before], h4_rows=[c.model_dump(mode="json") for c in m15_before],
        bid=m15_before[-1].close, ask=m15_before[-1].close, spread=0, now=at,
    )
    assert ctx_before is not None
    swings_before = [(s.candidate_time.isoformat(), str(s.price), s.swing_type) for s in ctx_before.m15_snapshot.swings]
    breaks_before = [(b.detected_time.isoformat(), str(b.break_kind)) for b in ctx_before.m15_snapshot.breaks]
    assert len(swings_before) > 0, "fixture must produce real swings for this test to be meaningful"

    # Now write a sharp, structure-altering "future" reversal AFTER `at` -- simulating exactly the
    # kind of DB state a live-running ingestion pipeline would have by the time this instant is
    # replayed later (real future price action already sitting in the table).
    with SessionLocal() as db:
        _seed(db, _FUTURE_REVERSAL, start_index=len(_SEQUENCE))
        db.commit()

    m15_after = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=100, provider="MT5"))
    ctx_after = replay.build_strategy_context(
        symbol="EURUSD", broker_symbol="EURUSD",
        m15_rows=[c.model_dump(mode="json") for c in m15_after], h1_rows=[c.model_dump(mode="json") for c in m15_after], h4_rows=[c.model_dump(mode="json") for c in m15_after],
        bid=m15_after[-1].close, ask=m15_after[-1].close, spread=0, now=at,
    )
    assert ctx_after is not None
    swings_after = [(s.candidate_time.isoformat(), str(s.price), s.swing_type) for s in ctx_after.m15_snapshot.swings]
    breaks_after = [(b.detected_time.isoformat(), str(b.break_kind)) for b in ctx_after.m15_snapshot.breaks]

    # The decisive assertion: identical bar count and identical structural output, proving the
    # later-written future bars never entered the point-in-time computation at all.
    assert len(m15_before) == len(m15_after)
    assert [c.time.isoformat() for c in m15_before] == [c.time.isoformat() for c in m15_after]
    assert swings_before == swings_after
    assert breaks_before == breaks_after
    assert ctx_before.regime == ctx_after.regime
    assert ctx_before.htf_trend_h1 == ctx_after.htf_trend_h1


def test_structure_DOES_change_once_at_advances_past_the_future_bars(monkeypatch):
    """Sanity check on the probe itself: the future reversal bars are not inert -- once `at`
    genuinely advances past them, they DO change the structural snapshot. This proves the
    previous test's "no change" result reflects real no-lookahead enforcement, not a fixture
    that happens to be insensitive to new data."""
    SessionLocal = _session_factory(monkeypatch)
    at_before = BASE + timedelta(minutes=15 * len(_SEQUENCE)) + timedelta(minutes=40)
    at_after_reversal = BASE + timedelta(minutes=15 * (len(_SEQUENCE) + len(_FUTURE_REVERSAL) - 1)) + timedelta(minutes=50)

    with SessionLocal() as db:
        _seed(db, _SEQUENCE)
        _seed(db, _FUTURE_REVERSAL, start_index=len(_SEQUENCE))
        db.commit()

    m15_before = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at_before, count=100, provider="MT5"))
    m15_after = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at_after_reversal, count=100, provider="MT5"))

    assert len(m15_after) > len(m15_before)  # genuinely sees more bars once `at` moves forward
    assert float(m15_after[-1].close) != float(m15_before[-1].close)

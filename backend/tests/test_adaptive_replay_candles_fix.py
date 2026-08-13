"""Regression test locking in the root-cause fix for the max_achieved_r=0.0 bug (Forex/MT5
roadmap, profit-retention investigation).

Root cause: _replay_candles() used to call adapter.candles(), which wraps MT5's
copy_rates_from_pos(symbol, tf, start=1, count) -- a REAL-TIME fetch of the most recent N bars
counting backward from NOW, unrelated to the trade's actual entry_time/exit_time. For any
already-closed trade, every returned candle postdated exit_time, so reconstruct_path()'s
[entry_time, exit_time] window filter discarded all of them -- mfe/mae silently stayed 0.0
forever, with no exception raised. Fixed by reading the persisted, point-in-time-safe
MT5CandleRevisionORM/MT5CanonicalCandleORM tables bounded to [entry_time - lead_in, exit_time]
instead of a live broker call.

These tests seed a HISTORICAL trade (entry/exit both well in the past relative to "now") together
with real candle rows covering that exact historical window PLUS decoy candle rows from "now" --
proving the fix reads the historical window, not whatever is most recent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management import service as adaptive_service
from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence.orm import MT5CandleRevisionORM
from backend.shared.db import Base

# The historical trade happened "days ago" relative to NOW (module import time) -- entry/exit
# are both fixed, far-past instants so a real "most recent N bars from now" fetch would never
# overlap them, exactly reproducing the conditions that caused the original bug.
ENTRY = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)  # 1 hour trade, M5 candles


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adaptive_service, "SessionLocal", SessionLocal)
    return SessionLocal


def _revision(db, *, bar_time: datetime, high: float, low: float, close: float, idx: int):
    db.add(MT5CandleRevisionORM(
        revision_id=f"REV_{idx}", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M5",
        bar_timestamp=bar_time, bar_timestamp_utc=bar_time, observed_at=bar_time, revision_number=1,
        open=close, high=high, low=low, close=close, finalized=True,
    ))


def _canonical(db, *, bar_time: datetime, high: float, low: float, close: float, idx: int):
    db.add(MT5CanonicalCandleORM(
        candle_id=f"CAN_{idx}", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M5",
        timestamp=bar_time, timestamp_utc=bar_time, open=close, high=high, low=low, close=close, quality="VALID",
    ))


def test_replay_candles_reads_the_historical_window_not_the_live_present(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # Real bars covering the historical [entry, exit] window -- one with a genuine favorable
        # excursion above entry, proving MFE will actually be computable this time.
        _revision(db, bar_time=ENTRY, high=1.1010, low=1.0995, close=1.1005, idx=1)
        _revision(db, bar_time=ENTRY + timedelta(minutes=5), high=1.1050, low=1.1000, close=1.1040, idx=2)  # favorable excursion
        _revision(db, bar_time=EXIT, high=1.1020, low=1.1005, close=1.1010, idx=3)
        # Decoy bars from "recent/now", far outside the historical window -- if the old
        # copy_rates_from_pos-style "most recent N bars" bug were still present, THESE (not the
        # real historical bars above) are what would have been returned.
        now = datetime.now(timezone.utc)
        _revision(db, bar_time=now - timedelta(minutes=5), high=9.0, low=8.0, close=8.5, idx=4)
        _revision(db, bar_time=now, high=9.5, low=8.5, close=9.0, idx=5)
        db.commit()

    import asyncio

    result = asyncio.run(adaptive_service._replay_candles("EURUSD", "M5", ENTRY, EXIT))
    assert len(result) == 3  # exactly the 3 historical bars, none of the 2 decoy "now" bars
    assert not any(row["high"] >= 9.0 for row in result)  # neither decoy ("now") bar leaked in
    assert any(abs(row["high"] - 1.1050) < 1e-9 for row in result)  # the favorable-excursion bar made it through


def test_replay_candles_falls_back_to_canonical_when_no_revision_covers_the_bar(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _canonical(db, bar_time=ENTRY, high=1.1030, low=1.0990, close=1.1000, idx=1)
        _canonical(db, bar_time=EXIT, high=1.1015, low=1.1000, close=1.1005, idx=2)
        db.commit()

    import asyncio

    result = asyncio.run(adaptive_service._replay_candles("EURUSD", "M5", ENTRY, EXIT))
    assert len(result) == 2
    assert any(abs(row["high"] - 1.1030) < 1e-9 for row in result)


def test_replay_candles_prefers_revision_over_canonical_for_the_same_bar(monkeypatch):
    """When both tables have a row for the same bar, the revision (finalized, authoritative)
    value wins -- matching outcomes.py's own established precedence."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _revision(db, bar_time=ENTRY, high=1.2000, low=1.1990, close=1.1995, idx=1)  # authoritative
        _canonical(db, bar_time=ENTRY, high=9.9999, low=9.9998, close=9.9998, idx=1)  # stale/wrong duplicate
        db.commit()

    import asyncio

    result = asyncio.run(adaptive_service._replay_candles("EURUSD", "M5", ENTRY, EXIT))
    assert len(result) == 1
    assert abs(result[0]["high"] - 1.2000) < 1e-9


def test_replay_candles_returns_empty_without_entry_time(monkeypatch):
    _session_factory(monkeypatch)
    import asyncio

    assert asyncio.run(adaptive_service._replay_candles("EURUSD", "M5", None, EXIT)) == []


def test_reconstruct_path_now_computes_real_nonzero_mfe(monkeypatch):
    """End-to-end proof: with real historical candles available, reconstruct_path() (the
    function whose output was silently always 0.0) now produces a genuine, non-zero
    max_achieved_r for a trade with a real favorable excursion."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _revision(db, bar_time=ENTRY, high=1.1010, low=1.0995, close=1.1005, idx=1)
        _revision(db, bar_time=ENTRY + timedelta(minutes=5), high=1.1050, low=1.1000, close=1.1040, idx=2)
        _revision(db, bar_time=EXIT, high=1.1020, low=1.1005, close=1.1010, idx=3)
        db.commit()

    import asyncio

    trade = {
        "trade_id": "T1", "symbol": "EURUSD", "direction": "LONG", "volume": 0.1,
        "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1100,
        "entry_time": ENTRY, "exit_time": EXIT, "actual_pnl": 10.0, "strategy_id": "test", "timeframe": "M5",
    }
    candles = asyncio.run(adaptive_service._replay_candles("EURUSD", "M5", ENTRY, EXIT))
    case = adaptive_service._trade_case(trade)
    path = adaptive_service.reconstruct_path(case, candles)
    assert path["max_achieved_r"] > 0  # previously always exactly 0.0 regardless of real price action

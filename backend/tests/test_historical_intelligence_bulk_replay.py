"""Regression tests for bulk_replay.py -- the chronological driver that walks replay_at across
real historical instants and persists fingerprints/outcomes (corpus-expansion directive, Phase
4-10). replay_at's own correctness (no-lookahead, quality tiers, production-code reuse) is
already covered by test_historical_intelligence_replay.py; these tests cover bulk_replay's own
responsibilities: walking/counting, idempotent persistence, restart-safe checkpointing, and
strategy_id filtering -- so replay_at is monkeypatched to a controlled fake here."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import bulk_replay, outcomes
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.mt5_strategies.context import build_strategy_context
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _mk_rows(n: int, *, base: float = 1.1000, wick: float = 0.0015) -> list[dict]:
    rows = []
    t = NOW - timedelta(minutes=15 * n)
    price = base
    for i in range(n):
        c = base
        o = price
        h = max(o, c) + wick
        low = min(o, c) - wick
        rows.append({"time": (t + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": low, "close": c, "tick_volume": 100, "spread": 1})
        price = c
    return rows


def _ctx():
    rows = _mk_rows(100)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"), now=NOW)
    assert ctx is not None
    return ctx


class _Quote:
    def __init__(self):
        self.bid = Decimal("1.1010")
        self.ask = Decimal("1.1012")
        self.spread = Decimal("0.0002")


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(bulk_replay, "SessionLocal", SessionLocal)
    monkeypatch.setattr(outcomes, "SessionLocal", SessionLocal)
    return SessionLocal


def _fake_replay_at_factory(*, fire_mtfai1: bool = True, fire_family: str | None = None, insufficient: bool = False):
    async def _fake(*, canonical_symbol, broker_symbol, at, provider="MT5", symbol_info=None, quote=None):
        if insufficient:
            return {"status": "INSUFFICIENT_HISTORY", "source": "RECONSTRUCTED", "as_of": at.isoformat(), "bars": {"m15": 5, "h1": 5, "h4": 5}}
        mtfai1 = {"direction": "LONG", "entry": 1.1010, "stop_loss": 1.0980, "take_profit": 1.1100} if fire_mtfai1 else {"direction": "NO_TRADE"}
        families = []
        if fire_family:
            families.append({"context": {"strategy_id": fire_family}, "direction": "LONG", "entry": 1.1010, "stop_loss": 1.0985, "take_profit": 1.1090})
        return {
            "status": "OK", "source": "RECONSTRUCTED", "as_of": at.isoformat(), "regime": "trending_up", "smc_evidence": {},
            "mtfai1": mtfai1, "families_candidates": families, "bars": {"m15": 100, "h1": 100, "h4": 100},
            "_ctx": _ctx(), "_quote": _Quote(),
        }
    return _fake


def test_walks_every_instant_in_window(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=False))
    start = NOW
    end = NOW + timedelta(hours=1)  # 4 instants at M15 stride
    result = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=start, end=end, resume=False))
    assert result["instants_walked"] == 4
    assert result["occurrences_new"] == 0  # NO_TRADE every instant


def test_insufficient_history_counted_not_errored(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(insufficient=True))
    result = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=30), resume=False))
    assert result["insufficient_history"] == 2
    assert result["errors"] == 0
    assert result["occurrences_new"] == 0


def test_firing_candidate_persists_fingerprint_and_outcome(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True))
    result = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15), resume=False))
    assert result["occurrences_new"] == 1
    with SessionLocal() as db:
        fps = db.query(HistoricalPatternFingerprintORM).all()
        assert len(fps) == 1
        assert fps[0].anchor_strategy == "mtfai1"
        assert fps[0].source_evaluation_id is None  # bulk-replay-sourced, never a live evaluation id
        outcomes_rows = db.query(HistoricalSetupOutcomeORM).all()
        assert len(outcomes_rows) == 1  # label_outcome always persists a row, even if PENDING/INSUFFICIENT_FUTURE_DATA


def test_multiple_strategies_fire_same_instant_produce_separate_occurrences(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True, fire_family="session_breakout"))
    result = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15), resume=False))
    assert result["occurrences_new"] == 2
    with SessionLocal() as db:
        strategies = {row.anchor_strategy for row in db.query(HistoricalPatternFingerprintORM).all()}
    assert strategies == {"mtfai1", "session_breakout"}


def test_strategy_ids_filter_restricts_what_is_persisted(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True, fire_family="session_breakout"))
    result = asyncio.run(bulk_replay.replay_symbol_history(
        canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15),
        strategy_ids=["session_breakout"], resume=False,
    ))
    assert result["occurrences_new"] == 1
    with SessionLocal() as db:
        strategies = {row.anchor_strategy for row in db.query(HistoricalPatternFingerprintORM).all()}
    assert strategies == {"session_breakout"}


def test_rerunning_same_window_is_idempotent(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True))
    args = dict(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15), resume=False)
    first = asyncio.run(bulk_replay.replay_symbol_history(**args))
    second = asyncio.run(bulk_replay.replay_symbol_history(**args))
    assert first["occurrences_new"] == 1
    assert second["occurrences_new"] == 0
    assert second["occurrences_existing"] == 1
    with SessionLocal() as db:
        assert db.query(HistoricalPatternFingerprintORM).count() == 1  # never duplicated


def test_resume_skips_already_covered_instants(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True))
    start = NOW
    end = NOW + timedelta(minutes=45)  # 3 instants

    first = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=start, end=end, resume=True))
    assert first["instants_walked"] == 3
    assert first["resumed_from"] is None

    second = asyncio.run(bulk_replay.replay_symbol_history(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=start, end=end, resume=True))
    assert second["instants_walked"] == 0  # nothing left to walk, checkpoint covers the whole window
    assert second["resumed_from"] is not None


def test_resume_false_rewalks_from_start_regardless_of_checkpoint(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(bulk_replay, "replay_at", _fake_replay_at_factory(fire_mtfai1=True))
    args = dict(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15))
    asyncio.run(bulk_replay.replay_symbol_history(**args, resume=False))
    second = asyncio.run(bulk_replay.replay_symbol_history(**args, resume=False))
    assert second["instants_walked"] == 1  # re-walked despite the checkpoint existing
    assert second["occurrences_new"] == 0  # but persistence is still idempotent
    with SessionLocal() as db:
        assert db.query(HistoricalPatternFingerprintORM).count() == 1

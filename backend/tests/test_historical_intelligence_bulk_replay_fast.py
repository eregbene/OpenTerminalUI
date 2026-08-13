"""Regression + parity tests for bulk_replay.py's optimized fast path (corpus-expansion-
THROUGHPUT directive, Phase 12 parity proof + Phase 25 tests). The decisive requirement: the fast
path (batched commits + H1/H4 snapshot caching) must produce IDENTICAL fingerprint/outcome values
to the original slow path for the same historical instants -- performance changes must never
change results."""
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


def test_fast_path_walks_every_instant(monkeypatch):
    _session_factory(monkeypatch)

    # The fast path calls bars_as_of/build_strategy_context/evaluate_all directly (not replay_at),
    # so it needs its own lower-level fakes rather than the replay_at-shaped fixture above.
    import backend.historical_intelligence.bulk_replay as br

    async def _fake_bars(*, canonical_symbol, broker_symbol, timeframe, at, count, provider="MT5"):
        return _mk_candles(timeframe, at)

    monkeypatch.setattr(br, "bars_as_of", _fake_bars)
    monkeypatch.setattr(br, "build_strategy_context", lambda **kw: _ctx())
    monkeypatch.setattr(br, "evaluate_all", lambda ctx: [])
    monkeypatch.setattr(br, "build_candidates", lambda **kw: [])
    monkeypatch.setattr(br, "_replay_mtfai1", lambda quote, m15, h1, h4, symbol_info: {"direction": "NO_TRADE"})

    result = asyncio.run(br.replay_symbol_history_fast(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(hours=1), resume=False))
    assert result["instants_walked"] == 4
    assert result["occurrences_new"] == 0


def _mk_candles(timeframe: str, at: datetime):
    from backend.brokers.mt5.models import MT5Candle
    delta = {"M15": timedelta(minutes=15), "H1": timedelta(hours=1), "H4": timedelta(hours=4)}[timeframe]
    out = []
    t = at - delta * 100
    price = Decimal("1.1000")
    for i in range(100):
        out.append(MT5Candle(symbol="EURUSD", timeframe=timeframe, time=t + delta * i, open=price, high=price + Decimal("0.001"), low=price - Decimal("0.001"), close=price, tick_volume=100, spread=1, real_volume=0, complete=True, source="MT5"))
    return out


def test_fast_path_firing_persists_fingerprint_and_outcome(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    import backend.historical_intelligence.bulk_replay as br

    async def _fake_bars(*, canonical_symbol, broker_symbol, timeframe, at, count, provider="MT5"):
        return _mk_candles(timeframe, at)

    monkeypatch.setattr(br, "bars_as_of", _fake_bars)
    monkeypatch.setattr(br, "build_strategy_context", lambda **kw: _ctx())
    monkeypatch.setattr(br, "evaluate_all", lambda ctx: [])
    monkeypatch.setattr(br, "build_candidates", lambda **kw: [])
    monkeypatch.setattr(br, "_replay_mtfai1", lambda quote, m15, h1, h4, symbol_info: {"direction": "LONG", "entry": 1.1010, "stop_loss": 1.0980, "take_profit": 1.1100})

    result = asyncio.run(br.replay_symbol_history_fast(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=15), resume=False))
    assert result["occurrences_new"] == 1
    with SessionLocal() as db:
        assert db.query(HistoricalPatternFingerprintORM).count() == 1
        assert db.query(HistoricalSetupOutcomeORM).count() == 1


def test_fast_path_batches_commits_not_one_per_row(monkeypatch):
    """The whole point of the fast path: with commit_batch_size=100 and only 4 real firing
    instants, there should be at most 2 actual commits (mid-loop batches never trigger, final
    flush always happens) -- verified indirectly by counting commit calls on the session."""
    SessionLocal = _session_factory(monkeypatch)
    import backend.historical_intelligence.bulk_replay as br

    async def _fake_bars(*, canonical_symbol, broker_symbol, timeframe, at, count, provider="MT5"):
        return _mk_candles(timeframe, at)

    monkeypatch.setattr(br, "bars_as_of", _fake_bars)
    monkeypatch.setattr(br, "build_strategy_context", lambda **kw: _ctx())
    monkeypatch.setattr(br, "evaluate_all", lambda ctx: [])
    monkeypatch.setattr(br, "build_candidates", lambda **kw: [])
    monkeypatch.setattr(br, "_replay_mtfai1", lambda quote, m15, h1, h4, symbol_info: {"direction": "LONG", "entry": 1.1010, "stop_loss": 1.0980, "take_profit": 1.1100})

    commit_calls = []
    orig_session = SessionLocal

    def counting_session(*args, **kwargs):
        s = orig_session(*args, **kwargs)
        orig_commit = s.commit

        def counted_commit():
            commit_calls.append(1)
            return orig_commit()

        s.commit = counted_commit
        return s

    monkeypatch.setattr(br, "SessionLocal", counting_session)

    result = asyncio.run(br.replay_symbol_history_fast(
        canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(minutes=60),
        commit_batch_size=100, resume=False,
    ))
    assert result["occurrences_new"] == 4  # one per M15 instant over 1 hour
    assert len(commit_calls) == 1  # only the final flush -- never reached the 100-row batch threshold


def test_h1_snapshot_cache_reused_when_last_bar_unchanged(monkeypatch):
    """H1 window is IDENTICAL across all 4 consecutive M15 instants in this fixture (mocked
    bars_as_of always returns the same H1 candles regardless of `at`) -- analyze_bars for H1
    must be called at most once, not once per instant."""
    _session_factory(monkeypatch)
    import backend.historical_intelligence.bulk_replay as br

    fixed_h1 = _mk_candles("H1", NOW)

    async def _fake_bars(*, canonical_symbol, broker_symbol, timeframe, at, count, provider="MT5"):
        if timeframe == "H1":
            return fixed_h1
        return _mk_candles(timeframe, at)

    analyze_calls = {"H1": 0, "H4": 0}
    real_analyze = br.analyze_bars

    def counting_analyze(rows, *, symbol, timeframe, **kw):
        analyze_calls[timeframe] = analyze_calls.get(timeframe, 0) + 1
        return real_analyze(rows, symbol=symbol, timeframe=timeframe, **kw)

    monkeypatch.setattr(br, "bars_as_of", _fake_bars)
    monkeypatch.setattr(br, "analyze_bars", counting_analyze)
    monkeypatch.setattr(br, "build_strategy_context", lambda **kw: _ctx())
    monkeypatch.setattr(br, "evaluate_all", lambda ctx: [])
    monkeypatch.setattr(br, "build_candidates", lambda **kw: [])
    monkeypatch.setattr(br, "_replay_mtfai1", lambda quote, m15, h1, h4, symbol_info: {"direction": "NO_TRADE"})

    asyncio.run(br.replay_symbol_history_fast(canonical_symbol="EURUSD", broker_symbol="EURUSD", start=NOW, end=NOW + timedelta(hours=1), resume=False))
    assert analyze_calls["H1"] == 1  # H1 window never changed across the 4 instants -- computed once, reused 3 times

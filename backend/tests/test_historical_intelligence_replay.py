"""Phase 2+ regression tests: strict no-lookahead proof, quality filtering, explicit
insufficient-history handling, proof that replay calls the REAL production strategy functions
rather than a reimplementation, and the richer EXACT/DIRECTION/GEOMETRY/REGIME/STRATEGY_PRESENT/
REPLAY_MISSING parity taxonomy (see test_historical_intelligence_point_in_time_integrity.py for
the revision/snapshot/finalization-specific tests)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5.orm import MT5CanonicalCandleORM, MT5CandidateEvaluationORM
from backend.historical_intelligence import parity, replay
from backend.historical_intelligence.orm import HistoricalReplayParityCheckORM
from backend.historical_intelligence.parity import _classify
from backend.shared.db import Base


async def _no_broker_offset(provider):
    return 0


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(replay, "SessionLocal", SessionLocal)
    monkeypatch.setattr(parity, "SessionLocal", SessionLocal)
    # bars_as_of() auto-resolves the broker UTC offset, which (absent any revision history) falls
    # back to a REAL broker call -- never exercise that in unit tests, offset=0 matches every
    # timestamp already being seeded as pre-corrected UTC in these tests.
    monkeypatch.setattr(replay, "_resolve_broker_offset_minutes", _no_broker_offset)
    return SessionLocal


def _seed_finalized_candle(db, *, provider, symbol, timeframe, t, close=1.1000, quality="VALID"):
    """Seeds a LEGACY-style canonical row (no revision history) that is comfortably finalized
    relative to any `at` used by these tests, so finalization-window timing never confounds the
    no-lookahead assertions below (see test_historical_intelligence_point_in_time_integrity.py
    for tests that specifically exercise the finalization-timing boundary)."""
    db.add(MT5CanonicalCandleORM(
        candle_id=f"{provider}:{symbol}:{timeframe}:{t.isoformat()}", provider=provider,
        canonical_symbol=symbol, broker_symbol=symbol, timeframe=timeframe, timestamp=t, timestamp_utc=t,
        open=close, high=close + 0.0005, low=close - 0.0005, close=close, quality=quality, finalized=True,
    ))


def test_bars_as_of_never_returns_future_data(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # Monday
    at = base + timedelta(days=2)  # far enough out that every seeded bar is finalized

    with SessionLocal() as db:
        # Bars fully closed before `at` -- must be included.
        for i in range(8):
            _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=base + timedelta(minutes=15 * i))
        # A bar whose OPEN is before `at` but whose CLOSE (open + 15m) is AFTER `at` -- the
        # still-forming bar as of `at`. Must be EXCLUDED even though its timestamp < at.
        still_forming = at - timedelta(minutes=5)
        _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=still_forming)
        # A bar entirely in the future relative to `at` -- must be EXCLUDED.
        _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=at + timedelta(hours=1))
        db.commit()

    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=100, provider="MT5"))

    assert len(bars) == 8  # only the fully-closed bars
    assert all(bar.time + timedelta(minutes=15) <= at for bar in bars)
    assert still_forming not in [b.time for b in bars]
    assert (at + timedelta(hours=1)) not in [b.time for b in bars]


def test_bars_as_of_excludes_invalid_quality(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    at = base + timedelta(days=1)
    with SessionLocal() as db:
        _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=base, quality="VALID")
        _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=base + timedelta(minutes=15), quality="INVALID")
        db.commit()

    bars = asyncio.run(replay.bars_as_of(canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15", at=at, count=100, provider="MT5"))
    assert len(bars) == 1
    assert bars[0].time == base


def test_replay_reports_insufficient_history_explicitly(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    at = base + timedelta(days=1)
    with SessionLocal() as db:
        # Only a handful of bars -- well under the 60/50/30 M15/H1/H4 minimums.
        for i in range(5):
            _seed_finalized_candle(db, provider="MT5", symbol="EURUSD", timeframe="M15", t=base + timedelta(minutes=15 * i))
        db.commit()

    result = asyncio.run(replay.replay_at(canonical_symbol="EURUSD", broker_symbol="EURUSD", at=at, provider="MT5"))
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["source"] == "RECONSTRUCTED"
    assert result["bars"]["m15"] == 5


def test_replay_calls_the_real_production_strategy_functions_not_a_reimplementation():
    """Structural proof (not behavioral) that replay.py never reimplements strategy logic --
    the functions it calls are the EXACT SAME objects the live autonomous engine imports."""
    from backend.mt5_strategies.context import build_strategy_context as live_build_context
    from backend.mt5_strategies.context import summarize_smc_evidence as live_summarize
    from backend.mt5_strategies.families import evaluate_all as live_evaluate_all
    from backend.mt5_strategies.fusion import build_candidates as live_build_candidates

    from backend.historical_intelligence.replay import build_strategy_context as replay_build_context
    from backend.historical_intelligence.replay import summarize_smc_evidence as replay_summarize
    from backend.historical_intelligence.replay import evaluate_all as replay_evaluate_all
    from backend.historical_intelligence.replay import build_candidates as replay_build_candidates

    assert replay_build_context is live_build_context
    assert replay_summarize is live_summarize
    assert replay_evaluate_all is live_evaluate_all
    assert replay_build_candidates is live_build_candidates

    # _score_candidate (MTFAI1) is imported lazily inside _replay_mtfai1 -- verify by source
    # inspection that it's an import, not a redefinition.
    import inspect

    source = inspect.getsource(replay._replay_mtfai1)
    assert "from backend.brokers.mt5.autonomous import _score_candidate" in source


# --- Parity taxonomy: pure classification logic (no DB/replay involved) ---------------------


def test_classify_exact_match_requires_direction_geometry_and_regime():
    assert _classify(strategy_present=True, direction_match=True, geometry_match=True, regime_match=True) == "EXACT_MATCH"


def test_classify_direction_match_when_geometry_or_regime_differ():
    assert _classify(strategy_present=True, direction_match=True, geometry_match=False, regime_match=False) == "DIRECTION_MATCH"
    assert _classify(strategy_present=True, direction_match=True, geometry_match=True, regime_match=False) == "DIRECTION_MATCH"


def test_classify_geometry_match_when_direction_differs():
    assert _classify(strategy_present=True, direction_match=False, geometry_match=True, regime_match=False) == "GEOMETRY_MATCH"


def test_classify_regime_match_when_only_regime_lines_up():
    assert _classify(strategy_present=True, direction_match=False, geometry_match=False, regime_match=True) == "REGIME_MATCH"


def test_classify_strategy_present_when_nothing_else_lines_up():
    assert _classify(strategy_present=True, direction_match=False, geometry_match=False, regime_match=False) == "STRATEGY_PRESENT"


def test_classify_replay_missing_when_strategy_absent():
    assert _classify(strategy_present=False, direction_match=True, geometry_match=True, regime_match=True) == "REPLAY_MISSING"


# --- End-to-end verify_parity, exercising replay_for_evaluation + persistence ----------------


def _seed_live_evaluation(db, *, evaluation_id, symbol="EURUSD", direction="LONG", strategy="mtfai1", regime="trending_up"):
    db.add(MT5CandidateEvaluationORM(
        evaluation_id=evaluation_id, cycle_id="C1", account_id="demo_10k", candidate_id=f"CAND_{evaluation_id}",
        symbol=symbol, broker_symbol=symbol, direction=direction, overall_confidence=80.0, confidence_band="80-84",
        proposed_entry=1.1000, proposed_stop_loss=1.0980, proposed_take_profit=1.1040,
        strategy=strategy, market_regime=regime, created_at=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
    ))


def test_parity_exact_match_when_direction_geometry_and_regime_all_agree(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_live_evaluation(db, evaluation_id="EVAL_1", regime="trending_up")
        db.commit()

    async def _fake_replay_for_evaluation(evaluation_id, **kwargs):
        return {"status": "OK", "source": "SNAPSHOT", "regime": "trending_up", "bars": {"m15": 100, "h1": 100, "h4": 100},
                "mtfai1": {"strategy_id": "mtfai1", "direction": "LONG", "entry": 1.1000, "stop_loss": 1.0980}, "families_candidates": []}

    monkeypatch.setattr(parity, "replay_for_evaluation", _fake_replay_for_evaluation)

    result = asyncio.run(parity.verify_parity("EVAL_1"))
    assert result["verdict"] == "EXACT_MATCH"
    assert result["diff_detail"]["regime_match"] is True

    with SessionLocal() as db:
        rows = db.query(HistoricalReplayParityCheckORM).all()
    assert len(rows) == 1
    assert rows[0].verdict == "EXACT_MATCH"


def test_parity_direction_match_when_regime_disagrees(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_live_evaluation(db, evaluation_id="EVAL_2", regime="trending_up")
        db.commit()

    async def _fake_replay_for_evaluation(evaluation_id, **kwargs):
        return {"status": "OK", "source": "RECONSTRUCTED", "regime": "ranging", "bars": {"m15": 100, "h1": 100, "h4": 100},
                "mtfai1": {"strategy_id": "mtfai1", "direction": "LONG", "entry": 1.1000, "stop_loss": 1.0980}, "families_candidates": []}

    monkeypatch.setattr(parity, "replay_for_evaluation", _fake_replay_for_evaluation)

    result = asyncio.run(parity.verify_parity("EVAL_2"))
    assert result["verdict"] == "DIRECTION_MATCH"
    assert result["diff_detail"]["regime_match"] is False


def test_parity_geometry_match_when_direction_disagrees_but_levels_align(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_live_evaluation(db, evaluation_id="EVAL_3", direction="LONG", regime="trending_up")
        db.commit()

    async def _fake_replay_for_evaluation(evaluation_id, **kwargs):
        return {"status": "OK", "source": "RECONSTRUCTED", "regime": "ranging", "bars": {"m15": 100, "h1": 100, "h4": 100},
                "mtfai1": {"strategy_id": "mtfai1", "direction": "SHORT", "entry": 1.1000, "stop_loss": 1.0980}, "families_candidates": []}

    monkeypatch.setattr(parity, "replay_for_evaluation", _fake_replay_for_evaluation)

    result = asyncio.run(parity.verify_parity("EVAL_3"))
    assert result["verdict"] == "GEOMETRY_MATCH"


def test_parity_replay_missing_when_strategy_never_fires(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_live_evaluation(db, evaluation_id="EVAL_4", strategy="ema_trend")
        db.commit()

    async def _fake_replay_for_evaluation(evaluation_id, **kwargs):
        return {"status": "OK", "source": "RECONSTRUCTED", "regime": "trending_up", "bars": {"m15": 100, "h1": 100, "h4": 100},
                "mtfai1": {"direction": None}, "families_candidates": []}

    monkeypatch.setattr(parity, "replay_for_evaluation", _fake_replay_for_evaluation)

    result = asyncio.run(parity.verify_parity("EVAL_4"))
    assert result["verdict"] == "REPLAY_MISSING"


def test_parity_batch_reports_independent_percentages_not_one_number(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_live_evaluation(db, evaluation_id="EVAL_5", regime="trending_up")
        _seed_live_evaluation(db, evaluation_id="EVAL_6", strategy="ema_trend")
        db.commit()

    async def _fake_replay_for_evaluation(evaluation_id, **kwargs):
        if evaluation_id == "EVAL_5":
            return {"status": "OK", "source": "SNAPSHOT", "regime": "trending_up", "bars": {"m15": 100, "h1": 100, "h4": 100},
                    "mtfai1": {"strategy_id": "mtfai1", "direction": "LONG", "entry": 1.1000, "stop_loss": 1.0980}, "families_candidates": []}
        return {"status": "OK", "source": "RECONSTRUCTED", "regime": "trending_up", "bars": {"m15": 100, "h1": 100, "h4": 100},
                "mtfai1": {"direction": None}, "families_candidates": []}

    monkeypatch.setattr(parity, "replay_for_evaluation", _fake_replay_for_evaluation)

    summary = asyncio.run(parity.run_parity_batch(["EVAL_5", "EVAL_6"]))
    assert summary["total"] == 2
    assert summary["verdict_counts"]["EXACT_MATCH"] == 1
    assert summary["verdict_counts"]["REPLAY_MISSING"] == 1
    assert "regime_match_pct" in summary["parity_percentages"]
    assert "direction_match_pct" in summary["parity_percentages"]
    assert "geometry_match_pct" in summary["parity_percentages"]
    assert "strategy_present_pct" in summary["parity_percentages"]
    assert summary["source_counts"] == {"SNAPSHOT": 1, "RECONSTRUCTED": 1}

"""Bensim -- Adaptive Manager V3 continuation, Part 9: automatic DEMO safety circuit.

Deliberately separate from circuit_breaker.py (operational malfunction only) and lifecycle.py
(human-approval-gated). This module is a genuinely automatic ACTIVE_DEMO -> SHADOW demotion,
gated on real sample size and materially-bad, recent-window-confirmed evidence -- never a
promotion, never LIVE-affecting, never tripped on a handful of losses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.mt5_strategies import demo_safety_circuit
from backend.mt5_strategies.demo_safety_circuit import (
    MT5DemoSafetyCircuitStateORM,
    SafetyEvaluation,
    evaluate_and_apply_all,
    evaluate_strategy,
    is_tripped,
    reset_trip,
)
from backend.shared.db import Base
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(demo_safety_circuit, "SessionLocal", session_factory)
    return session_factory


def _insert_trade(db, *, strategy_id: str, r: float, created_at: datetime, outcome_type: str = "EXECUTED") -> None:
    row = MT5CandidateEvaluationORM(
        evaluation_id=f"eval_{strategy_id}_{created_at.timestamp()}_{r}",
        cycle_id="cycle_1",
        candidate_id=f"cand_{strategy_id}_{created_at.timestamp()}_{r}",
        created_at=created_at,
        symbol="EURUSD",
        broker_symbol="EURUSD",
        direction="LONG",
        strategy=strategy_id,
        overall_confidence=80.0,
        confidence_band="HIGH",
        outcome_type=outcome_type,
        realized_r=r,
    )
    db.add(row)


def _seed_trades(db, strategy_id: str, rs: list[float], *, start: datetime | None = None) -> None:
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, r in enumerate(rs):
        _insert_trade(db, strategy_id=strategy_id, r=r, created_at=start + timedelta(hours=i))


def test_insufficient_sample_never_trips(db_session):
    with db_session() as db:
        _seed_trades(db, "breakout", [-1.0] * 10)
        db.commit()
    evaluation = evaluate_strategy("breakout")
    assert evaluation.should_trip is False
    assert "INSUFFICIENT_SAMPLE" in evaluation.reason


def test_good_evidence_never_trips_even_at_full_sample(db_session):
    with db_session() as db:
        _seed_trades(db, "smc_continuation", [0.3, -0.2] * 20)
        db.commit()
    evaluation = evaluate_strategy("smc_continuation")
    assert evaluation.sample_size == 40
    assert evaluation.should_trip is False
    assert evaluation.reason == "WITHIN_ACCEPTABLE_RANGE"


def test_materially_bad_and_recent_confirmed_trips(db_session):
    with db_session() as db:
        # 40 losing trades, deeply negative expectancy/PF, all recent -- clearly materially bad.
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    evaluation = evaluate_strategy("session_breakout")
    assert evaluation.sample_size == 40
    assert evaluation.should_trip is True
    assert "MATERIALLY_BAD_EVIDENCE" in evaluation.reason


def test_bad_overall_but_recovered_recent_window_does_not_trip(db_session):
    with db_session() as db:
        # First 30 trades badly negative (old, stale patch), most recent 20 solidly positive --
        # the recent-window confirmation must prevent a trip here (the strategy has recovered).
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _seed_trades(db, "ema_trend", [-1.0] * 30, start=start)
        _seed_trades(db, "ema_trend", [0.5] * 20, start=start + timedelta(hours=30))
        db.commit()
    evaluation = evaluate_strategy("ema_trend")
    assert evaluation.should_trip is False
    assert "RECENT_WINDOW_NOT_CONFIRMED" in evaluation.reason


def test_tiny_negative_deviation_around_zero_does_not_trip(db_session):
    with db_session() as db:
        _seed_trades(db, "vwap_reversion", [-0.02, 0.01] * 20)
        db.commit()
    evaluation = evaluate_strategy("vwap_reversion")
    assert evaluation.should_trip is False


def test_cutover_excludes_stale_pre_activation_history(db_session):
    with db_session() as db:
        cutover = datetime(2026, 8, 25, tzinfo=timezone.utc)
        # Stale pre-cutover history: catastrophic. Fresh post-cutover: still too small a sample.
        _seed_trades(db, "breakout", [-2.0] * 40, start=cutover - timedelta(days=10))
        _seed_trades(db, "breakout", [0.4] * 5, start=cutover + timedelta(hours=1))
        db.commit()
    evaluation = evaluate_strategy("breakout", since=cutover)
    assert evaluation.sample_size == 5
    assert evaluation.should_trip is False
    assert "INSUFFICIENT_SAMPLE" in evaluation.reason


def test_apply_evaluation_persists_and_trips_once(db_session):
    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    evaluation = evaluate_strategy("session_breakout")
    first = demo_safety_circuit.apply_evaluation("session_breakout", evaluation)
    assert first is True
    assert is_tripped("session_breakout") is True

    # Re-applying the same (still-bad) evaluation must not re-log/re-trip -- idempotent.
    second = demo_safety_circuit.apply_evaluation("session_breakout", evaluation)
    assert second is False


def test_never_auto_promotes(db_session):
    """Once tripped, a SUBSEQUENT good evaluation must never un-trip it automatically -- only
    reset_trip() (an explicit human action) can."""
    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    bad_eval = evaluate_strategy("session_breakout")
    demo_safety_circuit.apply_evaluation("session_breakout", bad_eval)
    assert is_tripped("session_breakout") is True

    good_eval = SafetyEvaluation("session_breakout", 40, 0.5, 2.0, -1.0, 0.5, False, "WITHIN_ACCEPTABLE_RANGE", {})
    demo_safety_circuit.apply_evaluation("session_breakout", good_eval)
    assert is_tripped("session_breakout") is True  # still tripped -- no auto-promotion


def test_reset_trip_clears_and_is_logged(db_session):
    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    evaluation = evaluate_strategy("session_breakout")
    demo_safety_circuit.apply_evaluation("session_breakout", evaluation)
    assert is_tripped("session_breakout") is True

    reset = reset_trip("session_breakout", reset_by="operator@example.com")
    assert reset is True
    assert is_tripped("session_breakout") is False

    # Resetting an already-untripped strategy is a no-op, not an error.
    assert reset_trip("session_breakout", reset_by="operator@example.com") is False


def test_is_tripped_defaults_false_for_unknown_strategy(db_session):
    assert is_tripped("some_never_evaluated_strategy") is False


def test_circuit_disabled_via_env_short_circuits_is_tripped(db_session, monkeypatch):
    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    evaluation = evaluate_strategy("session_breakout")
    demo_safety_circuit.apply_evaluation("session_breakout", evaluation)
    assert is_tripped("session_breakout") is True

    monkeypatch.setattr(demo_safety_circuit, "CIRCUIT_ENABLED", False)
    assert is_tripped("session_breakout") is False


def test_evaluate_and_apply_all_handles_multiple_strategies_independently(db_session):
    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        _seed_trades(db, "smc_continuation", [0.3, -0.2] * 20)
        db.commit()
    results = evaluate_and_apply_all(["session_breakout", "smc_continuation"])
    assert results["session_breakout"]["newly_tripped"] is True
    assert results["smc_continuation"]["newly_tripped"] is False
    assert is_tripped("session_breakout") is True
    assert is_tripped("smc_continuation") is False


def test_activation_status_reflects_trip(db_session, monkeypatch):
    from backend.mt5_strategies import models

    with db_session() as db:
        _seed_trades(db, "session_breakout", [-1.0] * 40)
        db.commit()
    evaluation = evaluate_strategy("session_breakout")
    demo_safety_circuit.apply_evaluation("session_breakout", evaluation)

    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_SESSION_BREAKOUT", "ACTIVE_MT5")
    # Even with an explicit ACTIVE_MT5 env override, a tripped safety circuit forces SHADOW_MT5 --
    # only reset_trip() (an explicit human action) can clear it, never the env var alone.
    assert models.activation_status("session_breakout") == models.SHADOW_MT5

    reset_trip("session_breakout", reset_by="operator@example.com")
    assert models.activation_status("session_breakout") == models.ACTIVE_MT5

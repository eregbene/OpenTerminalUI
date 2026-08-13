"""Adaptive Manager multi-neighbor analog intelligence regression tests.

Covers: hard-match enforcement (strategy/symbol/direction), only RESOLVED post-exit
counterfactuals count as neighbors, effective sample size never exceeds raw count,
NO_GOOD_HISTORICAL_ANALOG when nothing qualifies, and the recommendation function only ever
returns an existing action-family label (never invents a new one)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM
from backend.historical_intelligence import adaptive_similarity as asim
from backend.historical_intelligence import adaptive_statistics
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adaptive_statistics, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed(db, *, idx: int, strategy: str = "mtfai1", symbol: str = "EURUSD", direction: str = "LONG",
          regime: str = "trending_up", current_r: float = 0.6, max_achieved_r: float = 0.8,
          post_exit_status: str = "RESOLVED", reached_plus_1r: bool = True, reversed_strongly: bool = False, additional_r: float = 0.3):
    position_id = f"POS{idx}"
    db.add(AdaptivePositionBaselineORM(position_id=position_id, account_id="demo_10k", symbol=symbol, direction=direction, original_strategy=strategy, created_at=NOW))
    db.add(AdaptiveManagementEventORM(
        event_id=f"EVT{idx}", account_id="demo_10k", cycle_run_id="C1", created_at=NOW, position_id=position_id, symbol=symbol, direction=direction,
        current_r=current_r, max_achieved_r=max_achieved_r, min_achieved_r=0.0, action_type="HOLD", action_category="NO_ACTION", action_status="OK",
        market_regime=regime, is_at_or_beyond_breakeven=False, is_trailing_action=False, strategy=strategy,
    ))
    db.add(AdaptiveManagerCounterfactualORM(
        position_id=position_id, account_id="demo_10k", symbol=symbol, post_exit_status=post_exit_status,
        post_exit_reached_plus_1r=reached_plus_1r, post_exit_reversed_strongly=reversed_strongly, post_exit_additional_r_available=additional_r,
    ))


QUERY = {"current_regime": "trending_up", "mfe_bucket": None, "current_r_bucket": None, "elapsed_bucket": None, "session": None, "be_state": False}


def test_hard_match_enforces_strategy_symbol_direction(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed(db, idx=i, strategy="mtfai1", symbol="EURUSD", direction="LONG")
        for i in range(15, 20):
            _seed(db, idx=i, strategy="momentum", symbol="EURUSD", direction="LONG")  # different strategy
        db.commit()

    neighbors = asim.find_similar_states(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY, min_similarity=0.0, top_k=100)
    assert len(neighbors) == 15


def test_only_resolved_post_exit_counted(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(10):
            _seed(db, idx=i, post_exit_status="RESOLVED")
        for i in range(10, 15):
            _seed(db, idx=i, post_exit_status="PENDING")
        db.commit()

    neighbors = asim.find_similar_states(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY, min_similarity=0.0, top_k=100)
    assert len(neighbors) == 10


def test_effective_sample_size_never_exceeds_raw_count(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(20):
            _seed(db, idx=i)
        db.commit()

    stats = asim.weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY)
    assert stats["effective_sample_size"] <= stats["raw_neighbor_count"]


def test_no_good_analog_when_nothing_qualifies(monkeypatch):
    _session_factory(monkeypatch)
    stats = asim.weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY)
    assert stats["status"] == "NO_GOOD_HISTORICAL_ANALOG"


def test_recommendation_insufficient_below_sample_floor(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(5):
            _seed(db, idx=i)
        db.commit()

    stats = asim.weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY)
    assert asim.historical_management_recommendation(stats) == asim.RECOMMENDATION_INSUFFICIENT


def test_recommendation_protect_when_reversal_likely(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(25):
            _seed(db, idx=i, reached_plus_1r=False, reversed_strongly=True, additional_r=-0.3)
        db.commit()

    stats = asim.weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY)
    assert asim.historical_management_recommendation(stats) == asim.RECOMMENDATION_PROTECT_TRAIL_EXIT


def test_recommendation_hold_when_continuation_likely(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(25):
            _seed(db, idx=i, reached_plus_1r=True, reversed_strongly=False, additional_r=0.5)
        db.commit()

    stats = asim.weighted_state_statistics(strategy="mtfai1", symbol="EURUSD", direction="LONG", query_fields=QUERY)
    assert asim.historical_management_recommendation(stats) == asim.RECOMMENDATION_HOLD


def test_recommendation_never_returns_a_type_outside_existing_taxonomy(monkeypatch):
    valid = {asim.RECOMMENDATION_HOLD, asim.RECOMMENDATION_LIGHT_PROTECTION, asim.RECOMMENDATION_PROTECT_TRAIL_EXIT, asim.RECOMMENDATION_INSUFFICIENT}
    for status, ess, rt, rev, add_r, p1r in [
        ("OK", 30, 0.1, 0.1, 0.2, 0.6), ("OK", 30, 0.6, 0.1, 0.2, 0.6), ("OK", 30, 0.1, 0.6, 0.2, 0.6),
        ("OK", 30, 0.1, 0.1, -0.1, 0.6), ("NO_GOOD_HISTORICAL_ANALOG", 0, None, None, None, None),
    ]:
        stats = {"status": status, "effective_sample_size": ess, "probability_round_trip": rt, "probability_reversal": rev, "expected_additional_r": add_r, "probability_reach_plus_1r": p1r}
        assert asim.historical_management_recommendation(stats) in valid

"""Bensim -- Adaptive Manager V3, Parts 4-7: strategy-specific management policy profiles.
Pure functions of strategy_id + env vars -- no DB, no mocking needed."""
from __future__ import annotations

from backend.adaptive_management.strategy_profiles import get_profile, strategy_param


def test_untiered_strategy_gets_all_none_profile_ie_unchanged_global_defaults():
    profile = get_profile("some_future_strategy")
    assert profile.breakeven_r is None
    assert profile.partial_profit_r is None
    assert profile.partial_profit_fraction is None
    assert profile.trail_r is None
    assert profile.mfe_partial_enabled is False


def test_missing_strategy_id_gets_all_none_profile():
    profile = get_profile(None)
    assert profile.breakeven_r is None
    assert profile.mfe_partial_enabled is False


def test_strategy_param_falls_through_to_global_default_when_unset():
    assert strategy_param("some_future_strategy", "breakeven_r", 1.0) == 1.0
    assert strategy_param(None, "trail_r", 1.5) == 1.5


def test_trend_pullback_gives_more_room_than_global_default():
    profile = get_profile("trend_pullback")
    assert profile.breakeven_r == 1.5
    assert profile.trail_r == 2.0
    assert strategy_param("trend_pullback", "breakeven_r", 1.0) == 1.5


def test_mean_reversion_captures_earlier_than_global_default():
    profile = get_profile("mean_reversion")
    assert profile.breakeven_r == 0.6
    assert profile.partial_profit_r == 0.35
    assert profile.mfe_partial_enabled is True
    assert profile.mfe_partial_fraction == 0.6


def test_vwap_reversion_matches_mean_reversion_capture_posture():
    mr = get_profile("mean_reversion")
    vwap = get_profile("vwap_reversion")
    assert vwap.breakeven_r == mr.breakeven_r
    assert vwap.mfe_partial_enabled == mr.mfe_partial_enabled


def test_mtfai1_mfe_partial_reads_the_original_env_vars_verbatim(monkeypatch):
    """Critical backward-compatibility guarantee: mtfai1's real, already-proven MFE-partial
    behavior must be driven by the ORIGINAL MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED/_FRACTION env
    vars, not a new hardcoded default -- an operator turning the original flag off must still
    turn it off after this generalization."""
    monkeypatch.delenv("MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED", raising=False)
    profile_default_off = get_profile("mtfai1")
    assert profile_default_off.mfe_partial_enabled is False  # matches the original flag's own default

    monkeypatch.setenv("MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED", "true")
    monkeypatch.setenv("MT5_MTFAI1_V2_MFE_PARTIAL_FRACTION", "0.5")
    profile_on = get_profile("mtfai1")
    assert profile_on.mfe_partial_enabled is True
    assert profile_on.mfe_partial_fraction == 0.5

    monkeypatch.setenv("MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED", "false")
    profile_off = get_profile("mtfai1")
    assert profile_off.mfe_partial_enabled is False


def test_new_strategy_profile_env_override_is_reversible(monkeypatch):
    monkeypatch.setenv("MT5_STRATEGY_PROFILE_TREND_PULLBACK_BREAKEVEN_R", "3.0")
    profile = get_profile("trend_pullback")
    assert profile.breakeven_r == 3.0


def test_strategy_id_is_case_insensitive():
    assert get_profile("TREND_PULLBACK").breakeven_r == get_profile("trend_pullback").breakeven_r


# --- Part 8: original-thesis capture -----------------------------------------------------
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management import service
from backend.adaptive_management.orm import AdaptivePositionStateORM, Base
from backend.adaptive_management.service import adaptive_management_service
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(service, "SessionLocal", SessionLocal)
    return SessionLocal


def test_capture_original_thesis_populates_from_matching_evaluation_row(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened_at = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        eval_row = MT5CandidateEvaluationORM(
            evaluation_id="eval1", cycle_id="cyc1", account_id="demo_10k", candidate_id="cand1",
            strategy="trend_pullback", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
            created_at=opened_at, overall_confidence=76.5, confidence_band="valid_autonomous",
            strategy_evidence={"confluence_mode": "EMA_ZONE", "stop_geometry": {"take_profit_basis": "atr_projected_move"}},
        )
        db.add(eval_row)
        db.commit()

        position = AdaptivePositionStateORM(position_id="POS1", symbol="EURUSD", direction="LONG", strategy_id="trend_pullback")
        adaptive_management_service._capture_original_thesis(db, position, symbol="EURUSD", direction="LONG", opened_at=opened_at)

        assert position.setup_subtype == "EMA_ZONE"
        assert position.original_target_type == "atr_projected_move"
        assert position.original_confidence == 76.5


def test_capture_original_thesis_leaves_fields_none_when_no_match(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened_at = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        position = AdaptivePositionStateORM(position_id="POS2", symbol="GBPUSD", direction="SHORT", strategy_id="mean_reversion")
        adaptive_management_service._capture_original_thesis(db, position, symbol="GBPUSD", direction="SHORT", opened_at=opened_at)

        assert position.setup_subtype is None
        assert position.original_target_type is None
        assert position.original_confidence is None


def test_capture_original_thesis_noop_without_opened_at(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        position = AdaptivePositionStateORM(position_id="POS3", symbol="EURUSD", direction="LONG", strategy_id="trend_pullback")
        adaptive_management_service._capture_original_thesis(db, position, symbol="EURUSD", direction="LONG", opened_at=None)
        assert position.setup_subtype is None

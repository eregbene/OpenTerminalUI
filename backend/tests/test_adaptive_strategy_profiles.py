"""Bensim -- Adaptive Manager V3, Parts 4-7: strategy-specific management policy profiles.
Pure functions of strategy_id + env vars -- no DB, no mocking needed."""
from __future__ import annotations

from backend.adaptive_management.strategy_profiles import get_profile, strategy_param, strategy_param_confidence_aware


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

import pytest
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


def test_capture_original_thesis_populates_structural_reference(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened_at = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        eval_row = MT5CandidateEvaluationORM(
            evaluation_id="eval2", cycle_id="cyc2", account_id="demo_10k", candidate_id="cand2",
            strategy="breakout", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
            created_at=opened_at, overall_confidence=70.0, confidence_band="observe_only",
            strategy_evidence={"stop_geometry": {"structural_reference": 1.1050, "take_profit_basis": "atr_flat_multiple"}},
        )
        db.add(eval_row)
        db.commit()

        position = AdaptivePositionStateORM(position_id="POS4", symbol="EURUSD", direction="LONG", strategy_id="breakout")
        adaptive_management_service._capture_original_thesis(db, position, symbol="EURUSD", direction="LONG", opened_at=opened_at)

        assert position.original_structural_reference == 1.1050


# --- Parts 1-7 (continuation): the 6 remaining strategies' evidence-based profiles -------------
def test_smc_continuation_profile_gives_real_but_reduced_room():
    profile = get_profile("smc_continuation")
    assert profile.breakeven_r == 1.3
    assert profile.trail_r == 1.8


def test_support_resistance_bounce_profile_captures_earlier_like_mean_reversion():
    sr = get_profile("support_resistance_bounce")
    mr = get_profile("mean_reversion")
    assert sr.breakeven_r == mr.breakeven_r
    assert sr.mfe_partial_enabled is True


def test_session_breakout_profile_is_tighter_and_has_structural_reacceptance():
    profile = get_profile("session_breakout")
    assert profile.breakeven_r == 0.8
    assert profile.trail_r == 1.3
    assert profile.structural_reacceptance_enabled is True


def test_breakout_profile_has_no_breakeven_override_but_has_structural_reacceptance():
    profile = get_profile("breakout")
    assert profile.breakeven_r is None  # generic management already measured +0.380R delta -- don't disturb it
    assert profile.trail_r is None
    assert profile.structural_reacceptance_enabled is True


def test_ema_trend_profile_matches_trend_pullback_posture():
    ema = get_profile("ema_trend")
    tp = get_profile("trend_pullback")
    assert ema.breakeven_r == tp.breakeven_r
    assert ema.trail_r == tp.trail_r


def test_liquidity_sweep_reversal_has_no_override_yet():
    """Explicit instruction: 'do not over-manage it simply because it temporarily retraces' --
    no override means it gets the standard global default, not a guessed number."""
    profile = get_profile("liquidity_sweep_reversal")
    assert profile.breakeven_r is None
    assert profile.trail_r is None
    assert profile.structural_reacceptance_enabled is False


def test_structural_reacceptance_reversible_via_env(monkeypatch):
    monkeypatch.setenv("MT5_STRATEGY_PROFILE_BREAKOUT_STRUCTURAL_REACCEPTANCE_ENABLED", "false")
    assert get_profile("breakout").structural_reacceptance_enabled is False
    monkeypatch.setenv("MT5_STRATEGY_PROFILE_SMC_CONTINUATION_STRUCTURAL_REACCEPTANCE_ENABLED", "true")
    assert get_profile("smc_continuation").structural_reacceptance_enabled is True


# --- structural-reacceptance candidate generation (service.py) ---------------------------------
def test_structural_reacceptance_close_fires_on_closed_candle_reacceptance(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.delenv("ADAPTIVE_MFE_MIN_R", raising=False)
    state = AdaptivePositionStateORM(position_id="SR_1")
    state.symbol = "EURUSD"
    state.direction = "LONG"
    state.strategy_id = "session_breakout"
    state.current_volume = 1.0
    state.entry_price = 1.1060
    state.current_sl = 1.1040
    state.original_sl = 1.1040
    state.current_tp = 1.1100
    state.original_structural_reference = 1.1050  # the broken session-high level
    state.max_achieved_r = 0.2
    payload = {"price_current": 1.1045}  # closed back BELOW the broken level -- reacceptance

    with SessionLocal() as db:
        actions = adaptive_management_service._evaluate_position(db, state, payload, {}, _candles_closing_at(1.1045))
    reacceptance_actions = [a for a in actions if a.action_type == "STRUCTURAL_REACCEPTANCE_CLOSE"]
    assert len(reacceptance_actions) == 1
    assert reacceptance_actions[0].requested_volume == pytest.approx(1.0)


def test_structural_reacceptance_close_does_not_fire_for_untagged_strategy(monkeypatch):
    """Same price action, but strategy_id has no structural_reacceptance_enabled profile --
    must not fire (byte-identical to before this feature existed)."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.delenv("ADAPTIVE_MFE_MIN_R", raising=False)
    state = AdaptivePositionStateORM(position_id="SR_2")
    state.symbol = "EURUSD"
    state.direction = "LONG"
    state.strategy_id = "trend_pullback"
    state.current_volume = 1.0
    state.entry_price = 1.1060
    state.current_sl = 1.1040
    state.original_sl = 1.1040
    state.current_tp = 1.1100
    state.original_structural_reference = 1.1050
    state.max_achieved_r = 0.2
    payload = {"price_current": 1.1045}

    with SessionLocal() as db:
        actions = adaptive_management_service._evaluate_position(db, state, payload, {}, _candles_closing_at(1.1045))
    assert not [a for a in actions if a.action_type == "STRUCTURAL_REACCEPTANCE_CLOSE"]


def _candles_closing_at(close_price: float) -> list[dict]:
    from datetime import timedelta as _td
    start = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(10):
        rows.append({"time": (start + _td(minutes=5 * i)).isoformat(), "open": close_price, "high": close_price + 0.0005, "low": close_price - 0.0005, "close": close_price, "tick_volume": 100})
    return rows


def test_confidence_aware_normal_tier_is_byte_identical_to_strategy_param():
    for confidence in (65.0, 70.0, 90.0, None):
        assert strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=confidence) == strategy_param("trend_pullback", "breakeven_r", 1.0)


def test_confidence_aware_moderate_tier_tightens_r_threshold():
    base = strategy_param("trend_pullback", "breakeven_r", 1.0)  # 1.5 (trend_pullback base profile)
    tightened = strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=62.0)
    assert tightened == pytest.approx(base * 0.8)
    assert tightened < base


def test_confidence_aware_aggressive_tier_tightens_more_than_moderate():
    moderate = strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=62.0)
    aggressive = strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=57.0)
    assert aggressive < moderate


def test_confidence_aware_boosts_fraction_fields_instead_of_tightening():
    base = strategy_param("mean_reversion", "partial_profit_fraction", 0.25)  # 0.4 (mean_reversion base profile)
    boosted = strategy_param_confidence_aware("mean_reversion", "partial_profit_fraction", 0.25, confidence=57.0)
    assert boosted == pytest.approx(min(0.95, base + 0.30))
    assert boosted > base


def test_confidence_aware_fraction_boost_never_exceeds_cap():
    boosted = strategy_param_confidence_aware("mean_reversion", "partial_profit_fraction", 0.25, confidence=57.0)
    assert boosted <= 0.95


def test_confidence_aware_disabled_via_env_restores_strategy_param(monkeypatch):
    monkeypatch.setenv("MT5_CONFIDENCE_AWARE_MANAGEMENT_ENABLED", "false")
    base = strategy_param("trend_pullback", "breakeven_r", 1.0)
    result = strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=57.0)
    assert result == base


def test_confidence_aware_thresholds_reversible_via_env(monkeypatch):
    monkeypatch.setenv("MT5_CONFIDENCE_MANAGEMENT_MODERATE_R_MULTIPLIER", "0.5")
    base = strategy_param("trend_pullback", "breakeven_r", 1.0)
    result = strategy_param_confidence_aware("trend_pullback", "breakeven_r", 1.0, confidence=62.0)
    assert result == pytest.approx(base * 0.5)

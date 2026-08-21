"""Priority 6: strategy lifecycle governance (backend/mt5_strategies/lifecycle.py). Covers
transitions, the human-approval requirement (recommendations never risk capital on their own),
strategy-version invalidation, degradation/recovery hysteresis, insufficient-sample behavior,
evidence gathering, and audit-history persistence.
"""
from __future__ import annotations

import pytest

from backend.mt5_strategies import lifecycle as lc
from backend.mt5_strategies.models import ACTIVE_MT5, SHADOW_MT5
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite

STRATEGY = "trend_pullback"


def _wire(monkeypatch: pytest.MonkeyPatch, *, activation: str = SHADOW_MT5, real_stats=None, shadow_stats=None, fingerprint: str = "fp-v1") -> None:
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(lc, "activation_status", lambda strategy_id: activation)
    monkeypatch.setattr(lc, "_real_trade_stats", lambda strategy_id, window_start: real_stats or {"sample_size": 0, "win_rate": None, "expectancy_r": None, "realized_usd": None, "avg_realized_usd": None})
    monkeypatch.setattr(lc, "_shadow_stats", lambda strategy_id, window_start: shadow_stats or {"sample_size": 0, "win_rate": None, "expectancy_r": None, "realized_usd": None, "avg_realized_usd": None})
    monkeypatch.setattr(lc, "strategy_version_fingerprint", lambda strategy_id: fingerprint)


def _stats(n: int, expectancy_r: float) -> dict:
    return {"sample_size": n, "win_rate": 0.5, "expectancy_r": expectancy_r, "realized_usd": expectancy_r * n * 25, "avg_realized_usd": expectancy_r * 25}


# --- initialization / state persistence --------------------------------------------------


def test_initialize_state_seeds_and_is_idempotent(monkeypatch):
    _wire(monkeypatch)
    first = lc.initialize_state(STRATEGY, lc.SHADOW, reason="seed")
    second = lc.initialize_state(STRATEGY, lc.ACTIVE, reason="should not overwrite")

    assert first["lifecycle_state"] == lc.SHADOW
    assert second["lifecycle_state"] == lc.SHADOW  # unchanged -- initialize never overwrites


def test_initialize_state_maps_activation_for_every_state(monkeypatch):
    _wire(monkeypatch)
    expected = {
        lc.RESEARCH: "DISABLED", lc.BACKTESTED: "DISABLED", lc.ROBUSTNESS_TESTED: "DISABLED",
        lc.SHADOW: SHADOW_MT5, lc.DEMO: SHADOW_MT5, lc.LIVE_ELIGIBLE: SHADOW_MT5,
        lc.ACTIVE: ACTIVE_MT5, lc.DEGRADED: ACTIVE_MT5, lc.SUSPENDED: SHADOW_MT5, lc.RETIRED: SHADOW_MT5,
    }
    for i, (state, activation) in enumerate(expected.items()):
        strategy_id = f"strat_{i}"
        result = lc.initialize_state(strategy_id, state, reason="test")
        assert result["mapped_activation"] == activation


def test_get_state_returns_research_default_when_uninitialized(monkeypatch):
    _wire(monkeypatch)
    state = lc.get_state("never_seeded")
    assert state["lifecycle_state"] == lc.RESEARCH


def test_every_transition_is_recorded_in_the_audit_history(monkeypatch):
    _wire(monkeypatch)
    lc.initialize_state(STRATEGY, lc.SHADOW, reason="seed")
    events = lc.events_for(STRATEGY)
    assert len(events) == 1
    assert events[0]["event_type"] == "TRANSITION"
    assert events[0]["to_state"] == lc.SHADOW


# --- promotion: recommendation only, never auto-applied to capital -----------------------


def test_evaluate_promotion_insufficient_sample_yields_no_recommendation(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(5, 0.5))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")

    result = lc.evaluate_promotion(STRATEGY)

    assert result["recommendation"] is None
    assert "INSUFFICIENT_SAMPLE" in result["reason"]


def test_evaluate_promotion_recommends_when_expectancy_clears_threshold(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")

    result = lc.evaluate_promotion(STRATEGY)

    assert result["recommendation"] == lc.PROMOTE_RECOMMENDATION
    assert result["candidate_next_state"] == lc.LIVE_ELIGIBLE


def test_evaluate_promotion_never_changes_lifecycle_state_directly(monkeypatch):
    # The whole point of the human-approval gate: a recommendation must never itself move
    # lifecycle_state (and never touches MT5_STRATEGY_ACTIVATION_<ID> at all).
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")

    lc.evaluate_promotion(STRATEGY)

    assert lc.get_state(STRATEGY)["lifecycle_state"] == lc.DEMO
    assert lc.get_state(STRATEGY)["pending_recommendation"] == lc.PROMOTE_RECOMMENDATION


def test_evaluate_promotion_below_threshold_recommends_nothing(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD - 0.2))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")

    result = lc.evaluate_promotion(STRATEGY)

    assert result["recommendation"] is None


def test_evaluate_promotion_does_not_apply_to_a_degraded_strategy(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, 1.0))
    lc.initialize_state(STRATEGY, lc.DEGRADED, reason="seed")

    result = lc.evaluate_promotion(STRATEGY)

    assert result["recommendation"] is None
    assert "evaluate_demotion" in result["reason"]


# --- human approval: the ONLY path that moves lifecycle_state, and it never touches capital


def test_apply_human_decision_approve_moves_to_the_recommended_state(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")
    lc.evaluate_promotion(STRATEGY)

    result = lc.apply_human_decision(STRATEGY, decision="APPROVE", approved_by="test_user")

    assert result["lifecycle_state"] == lc.LIVE_ELIGIBLE
    assert result["pending_recommendation"] is None


def test_apply_human_decision_reject_leaves_state_unchanged(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")
    lc.evaluate_promotion(STRATEGY)

    result = lc.apply_human_decision(STRATEGY, decision="REJECT", approved_by="test_user")

    assert result["lifecycle_state"] == lc.DEMO
    assert result["pending_recommendation"] is None


def test_apply_human_decision_requires_a_pending_recommendation(monkeypatch):
    _wire(monkeypatch)
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")

    with pytest.raises(ValueError):
        lc.apply_human_decision(STRATEGY, decision="APPROVE", approved_by="test_user")


def test_apply_human_decision_records_who_approved_it(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")
    lc.evaluate_promotion(STRATEGY)

    lc.apply_human_decision(STRATEGY, decision="APPROVE", approved_by="ben")

    events = lc.events_for(STRATEGY)
    approval = next(e for e in events if e["event_type"] == "HUMAN_APPROVED")
    assert approval["approved_by"] == "ben"


# --- demotion hysteresis: never flip on a single bad window -------------------------------


def test_evaluate_demotion_does_not_flag_degraded_on_the_first_negative_period(monkeypatch):
    _wire(monkeypatch, activation=ACTIVE_MT5, real_stats=_stats(30, lc.DEMOTE_EXPECTANCY_R_THRESHOLD - 0.1))
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")

    result = lc.evaluate_demotion(STRATEGY)

    assert result["recommendation"] is None
    assert lc.get_state(STRATEGY)["consecutive_negative_periods"] == 1


def test_evaluate_demotion_flags_degraded_after_hysteresis_periods(monkeypatch):
    _wire(monkeypatch, activation=ACTIVE_MT5, real_stats=_stats(30, lc.DEMOTE_EXPECTANCY_R_THRESHOLD - 0.1))
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")

    lc.evaluate_demotion(STRATEGY)  # period 1: no recommendation yet
    result = lc.evaluate_demotion(STRATEGY)  # period 2: hysteresis threshold reached

    assert result["recommendation"] == lc.DEMOTE_RECOMMENDATION
    assert result["candidate_next_state"] == lc.DEGRADED
    # still not applied -- state only changes via apply_human_decision
    assert lc.get_state(STRATEGY)["lifecycle_state"] == lc.ACTIVE


def test_evaluate_demotion_negative_period_resets_the_positive_counter(monkeypatch):
    _wire(monkeypatch, activation=ACTIVE_MT5, real_stats=_stats(30, 0.5))
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")
    lc.evaluate_demotion(STRATEGY)
    assert lc.get_state(STRATEGY)["consecutive_positive_periods"] == 1

    monkeypatch.setattr(lc, "_real_trade_stats", lambda strategy_id, window_start: _stats(30, lc.DEMOTE_EXPECTANCY_R_THRESHOLD - 0.1))
    lc.evaluate_demotion(STRATEGY)

    assert lc.get_state(STRATEGY)["consecutive_positive_periods"] == 0
    assert lc.get_state(STRATEGY)["consecutive_negative_periods"] == 1


def test_evaluate_demotion_recovery_requires_hysteresis_periods_too(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, 0.5))
    lc.initialize_state(STRATEGY, lc.DEGRADED, reason="seed")

    first = lc.evaluate_demotion(STRATEGY)
    assert first["recommendation"] is None

    second = lc.evaluate_demotion(STRATEGY)
    assert second["recommendation"] == lc.PROMOTE_RECOMMENDATION
    assert second["candidate_next_state"] == lc.DEMO  # recovery re-enters at DEMO, not straight to ACTIVE


def test_evaluate_demotion_insufficient_sample_yields_no_signal(monkeypatch):
    _wire(monkeypatch, activation=ACTIVE_MT5, real_stats=_stats(3, -1.0))
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")

    result = lc.evaluate_demotion(STRATEGY)

    assert result["recommendation"] is None
    assert "INSUFFICIENT_SAMPLE" in result["reason"]


# --- strategy-version awareness ------------------------------------------------------------


def test_check_version_consistency_flags_a_changed_fingerprint(monkeypatch):
    _wire(monkeypatch, fingerprint="fp-v1")
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")

    monkeypatch.setattr(lc, "strategy_version_fingerprint", lambda strategy_id: "fp-v2-changed")
    result = lc.check_version_consistency(STRATEGY)

    assert result["status"] == "REQUIRES_CLASSIFICATION"
    assert lc.get_state(STRATEGY)["version_status"] == "REQUIRES_CLASSIFICATION"


def test_evaluate_promotion_refuses_to_use_evidence_pending_version_classification(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, 1.0), fingerprint="fp-v1")
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")
    monkeypatch.setattr(lc, "strategy_version_fingerprint", lambda strategy_id: "fp-v2-changed")

    result = lc.evaluate_promotion(STRATEGY)

    assert result["recommendation"] is None
    assert "classify_version_change" in result["reason"]


def test_classify_version_change_refactor_only_preserves_state(monkeypatch):
    _wire(monkeypatch, fingerprint="fp-v1")
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")
    monkeypatch.setattr(lc, "strategy_version_fingerprint", lambda strategy_id: "fp-v2")
    lc.check_version_consistency(STRATEGY)

    result = lc.classify_version_change(STRATEGY, classification="REFACTOR_ONLY", approved_by="ben")

    assert result["lifecycle_state"] == lc.ACTIVE
    assert result["version_status"] == "CONSISTENT"


def test_classify_version_change_behavioral_resets_to_backtested(monkeypatch):
    _wire(monkeypatch, fingerprint="fp-v1")
    lc.initialize_state(STRATEGY, lc.ACTIVE, reason="seed")
    monkeypatch.setattr(lc, "strategy_version_fingerprint", lambda strategy_id: "fp-v2")
    lc.check_version_consistency(STRATEGY)

    result = lc.classify_version_change(STRATEGY, classification="BEHAVIORAL_CHANGE", approved_by="ben")

    assert result["lifecycle_state"] == lc.BACKTESTED
    assert result["version_status"] == "BEHAVIORAL_CHANGE_PENDING_REVALIDATION"


# --- evidence gathering ---------------------------------------------------------------------


def test_gather_evidence_lists_missing_evidence_explicitly(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats={"sample_size": 0, "win_rate": None, "expectancy_r": None, "realized_usd": None, "avg_realized_usd": None})

    evidence = lc.gather_evidence("strategy_not_in_audit_lookup")

    assert any("forward_performance" in item for item in evidence["missing_evidence"])
    assert any("historical_audit" in item for item in evidence["missing_evidence"])


def test_gather_evidence_uses_real_trades_when_active_and_shadow_when_not(monkeypatch):
    _wire(monkeypatch, activation=ACTIVE_MT5, real_stats=_stats(25, 0.3), shadow_stats=_stats(99, -9.0))
    evidence_active = lc.gather_evidence(STRATEGY)
    assert evidence_active["forward_performance"]["source"] == "REAL_TRADES"
    assert evidence_active["forward_performance"]["sample_size"] == 25

    monkeypatch.setattr(lc, "activation_status", lambda strategy_id: SHADOW_MT5)
    evidence_shadow = lc.gather_evidence(STRATEGY)
    assert evidence_shadow["forward_performance"]["source"] == "SHADOW_TRACKING"
    assert evidence_shadow["forward_performance"]["sample_size"] == 99


def test_explain_returns_a_full_non_opaque_summary(monkeypatch):
    _wire(monkeypatch, activation=SHADOW_MT5, shadow_stats=_stats(30, lc.PROMOTE_EXPECTANCY_R_THRESHOLD + 0.1))
    lc.initialize_state(STRATEGY, lc.DEMO, reason="seed")
    lc.evaluate_promotion(STRATEGY)

    summary = lc.explain(STRATEGY)

    assert summary["lifecycle_state"] == lc.DEMO
    assert summary["pending_recommendation"] == lc.PROMOTE_RECOMMENDATION
    assert summary["pending_recommendation_reason"]
    assert len(summary["recent_events"]) >= 1

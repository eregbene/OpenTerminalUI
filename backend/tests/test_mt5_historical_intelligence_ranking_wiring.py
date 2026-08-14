"""Regression tests for MT5AutonomousTradingService._apply_historical_intelligence
(Historical-Intelligence-Semantics-Audit directive): the fix that actually wires
entry_intelligence.evaluate_historical_intelligence's output into the real candidate-ranking
path, closing the gap where that function had zero production callers and its SUPPORT/DEFER/
REJECT verdicts were computed only for post-decision observability, never applied.

Tests _apply_historical_intelligence directly (pure-ish: one monkeypatched async call, then
in-place mutation of the candidate/confidence dicts passed in) rather than driving a full
run_cycle(), matching the established pattern in test_mt5_demo_diversity_cap.py for behavior
this narrow.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService


def _mk_service() -> MT5AutonomousTradingService:
    adapter = SimpleNamespace(config=SimpleNamespace(account_mode="DEMO"))
    return MT5AutonomousTradingService(adapter=adapter)


def _mk_candidate(**overrides) -> dict:
    base = {
        "broker_symbol": "EURUSD", "entry": "1.1000", "stop_loss": "1.0950", "take_profit": "1.1100",
        "context": {"strategy_id": "mtfai1", "strategy_family": "trend_multi_timeframe"},
        "rejection_reasons": [],
    }
    base.update(overrides)
    return base


def _mk_confidence(overall_score: float = 80.0) -> dict:
    return {"overall_score": overall_score, "band": "high"}


async def _run(service, candidate, confidence, evaluation):
    import backend.historical_intelligence.entry_intelligence as entry_intelligence_mod

    async def _fake_evaluate(**kwargs):
        return evaluation

    import backend.brokers.mt5.autonomous as autonomous_mod

    # Patch the name entry_intelligence.evaluate_historical_intelligence resolves to at
    # call time inside _apply_historical_intelligence's local import.
    import unittest.mock as mock

    with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _fake_evaluate):
        await service._apply_historical_intelligence(candidate, confidence)


@pytest.mark.asyncio
async def test_no_context_cached_is_a_safe_noop():
    service = _mk_service()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    # service._cycle_context_cache is empty -- no cached ctx for EURUSD.
    await service._apply_historical_intelligence(candidate, confidence)
    assert confidence["overall_score"] == 80.0
    assert candidate.get("historical_intelligence") is None
    assert candidate["rejection_reasons"] == []


@pytest.mark.asyncio
async def test_positive_evidence_raises_ranking_score(monkeypatch):
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 7.5, "defer_reject_reason": None}
    await _run(service, candidate, confidence, evaluation)
    assert confidence["overall_score"] == pytest.approx(87.5)
    assert candidate["ranking_score"] == pytest.approx(87.5)
    assert candidate["rejection_reasons"] == []
    assert candidate["historical_intelligence"] == evaluation


@pytest.mark.asyncio
async def test_negative_evidence_lowers_ranking_score(monkeypatch):
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "DEFER", "ranking_adjustment": -6.0, "defer_reject_reason": None}
    await _run(service, candidate, confidence, evaluation)
    assert confidence["overall_score"] == pytest.approx(74.0)
    assert candidate["rejection_reasons"] == []  # DEFER lowers rank, never excludes outright


@pytest.mark.asyncio
async def test_reject_verdict_adds_a_rejection_reason(monkeypatch):
    """The strongest, most reliable negative signal -- this is the one case that must feed the
    SAME exclusion mechanism BELOW_CONFIDENCE_THRESHOLD already uses (candidates with any
    rejection_reasons are filtered out of `eligible` before final selection)."""
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "REJECT", "ranking_adjustment": -10.0, "defer_reject_reason": "HISTORICAL_EVIDENCE_STRONGLY_NEGATIVE"}
    await _run(service, candidate, confidence, evaluation)
    assert "HISTORICAL_EVIDENCE_STRONGLY_NEGATIVE" in candidate["rejection_reasons"]


@pytest.mark.asyncio
async def test_neutral_evidence_changes_nothing():
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "UNAVAILABLE", "reason": "PATTERN_SAMPLE_INSUFFICIENT", "historical_decision": "HIST_INTEL_INSUFFICIENT", "ranking_adjustment": 0.0, "defer_reject_reason": None}
    await _run(service, candidate, confidence, evaluation)
    assert confidence["overall_score"] == 80.0
    assert candidate["rejection_reasons"] == []


@pytest.mark.asyncio
async def test_ranking_score_is_clamped_to_valid_range():
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(96.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 10.0, "defer_reject_reason": None}
    await _run(service, candidate, confidence, evaluation)
    assert confidence["overall_score"] == 100.0  # clamped, never exceeds the valid confidence range


@pytest.mark.asyncio
async def test_evaluation_exception_is_a_safe_noop(monkeypatch):
    """Never raises out of the ranking loop, never blocks a cycle -- matches
    entry_intelligence.py's own fail-open contract exactly."""
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)

    import unittest.mock as mock

    import backend.historical_intelligence.entry_intelligence as entry_intelligence_mod

    async def _boom(**kwargs):
        raise RuntimeError("simulated failure")

    with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _boom):
        await service._apply_historical_intelligence(candidate, confidence)

    assert confidence["overall_score"] == 80.0
    assert candidate.get("historical_intelligence") is None


@pytest.mark.asyncio
async def test_missing_strategy_id_is_a_safe_noop():
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate(context={})  # no strategy_id
    confidence = _mk_confidence(80.0)
    await service._apply_historical_intelligence(candidate, confidence)
    assert confidence["overall_score"] == 80.0

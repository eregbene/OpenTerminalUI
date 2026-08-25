"""Regression tests for MT5AutonomousTradingService._apply_historical_intelligence and
_build_context_for_historical_intelligence (Historical-Intelligence-Semantics-Audit directive):
the fix that actually wires entry_intelligence.evaluate_historical_intelligence's output into
the real candidate-ranking path (closing the "zero production callers" gap), plus the
Phase-2 fix for the real _cycle_context_cache coverage gap (mtfai1 candidates reach ranking
with no cached ctx whenever the regime is incompatible with every OTHER strategy family, since
Part 4's cheap_prefilter/regime_compatible optimization skips building the full multi-strategy
context in that case).

Tests _apply_historical_intelligence directly (pure-ish: one monkeypatched async call, then
in-place mutation of the candidate/confidence dicts passed in) rather than driving a full
run_cycle(), matching the established pattern in test_mt5_demo_diversity_cap.py for behavior
this narrow.
"""
from __future__ import annotations

import unittest.mock as mock
from types import SimpleNamespace

import pytest

import backend.brokers.mt5.autonomous as autonomous_mod
import backend.historical_intelligence.entry_intelligence as entry_intelligence_mod
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService


@pytest.fixture(autouse=True)
def _mtfai1_v2_disabled_by_default(monkeypatch):
    # Every test in this file except the test_mtfai1_v2_* ones below is about the GENERAL
    # HI-wiring behavior, using _mk_candidate's default strategy_id="mtfai1"/symbol="EURUSD" as a
    # convenient stand-in -- not about MTFAI1 V2 specifically. Forcing the flag off here keeps
    # them deterministic regardless of whatever MT5_MTFAI1_V2_ENABLED happens to be set to in the
    # real environment the suite runs in (the live deployed container currently has it true).
    # The V2-specific tests below re-enable it explicitly, which overrides this default.
    monkeypatch.setattr(autonomous_mod, "MT5_MTFAI1_V2_ENABLED", False)


def _mk_service() -> MT5AutonomousTradingService:
    adapter = SimpleNamespace(config=SimpleNamespace(account_mode="DEMO"))
    return MT5AutonomousTradingService(adapter=adapter)


def _mk_candidate(**overrides) -> dict:
    base = {
        "broker_symbol": "EURUSD", "entry": "1.1000", "stop_loss": "1.0950", "take_profit": "1.1100",
        "context": {"strategy_id": "mtfai1", "strategy_family": "trend_multi_timeframe", "symbol": "EURUSD", "bid": "1.1000", "ask": "1.1002", "spread": "0.0002"},
        "rejection_reasons": [],
    }
    base.update(overrides)
    return base


def _mk_confidence(overall_score: float = 80.0) -> dict:
    return {"overall_score": overall_score, "band": "high"}


async def _run(service, candidate, confidence, evaluation):
    async def _fake_evaluate(**kwargs):
        return evaluation

    # Real usage always sets candidate["ranking_score"] = confidence["overall_score"] BEFORE
    # calling _apply_historical_intelligence (see _rank_candidates_by_confidence) -- mirrored
    # here so rank_before/rank_after observability matches the real call sequence.
    candidate["ranking_score"] = confidence["overall_score"]
    with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _fake_evaluate):
        await service._apply_historical_intelligence(candidate, confidence)


@pytest.mark.asyncio
async def test_no_cycle_cache_falls_back_to_rebuild_and_still_evaluates(monkeypatch):
    """The Phase-2 fix: a missing self._cycle_context_cache entry must not silently no-op --
    it must attempt a real fallback rebuild first."""
    service = _mk_service()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    fake_ctx = SimpleNamespace()

    async def _fake_build(cand):
        return fake_ctx

    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 5.0, "defer_reject_reason": None}

    async def _fake_evaluate(**kwargs):
        assert kwargs["ctx"] is fake_ctx
        return evaluation

    with mock.patch.object(service, "_build_context_for_historical_intelligence", _fake_build):
        with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _fake_evaluate):
            await service._apply_historical_intelligence(candidate, confidence)

    assert candidate["historical_intelligence"]["context_source"] == "fallback_rebuild"
    assert confidence["overall_score"] == pytest.approx(85.0)


@pytest.mark.asyncio
async def test_context_truly_unavailable_records_explicit_marker_not_silent_skip():
    """Phase 3: 'do not silently skip candidates because cycle context is unavailable.' When
    the fallback rebuild also fails, the candidate must carry an explicit
    HIST_INTEL_CONTEXT_UNAVAILABLE marker, never an absent/None field."""
    service = _mk_service()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)
    # No _cycle_context_cache entry, and the real fallback build will fail against the fake
    # adapter (no real MT5/Redis connectivity) -- exercises the genuine failure path.
    await service._apply_historical_intelligence(candidate, confidence)
    assert confidence["overall_score"] == 80.0
    hi = candidate["historical_intelligence"]
    assert hi is not None
    assert hi["status"] == "UNAVAILABLE"
    assert hi["reason"] == "HIST_INTEL_CONTEXT_UNAVAILABLE"
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
    assert candidate["historical_intelligence"]["historical_decision"] == "SUPPORT"
    assert candidate["historical_intelligence"]["context_source"] == "cycle_cache"
    assert candidate["historical_intelligence"]["rank_before"] == 80.0
    assert candidate["historical_intelligence"]["rank_after"] == pytest.approx(87.5)


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
async def test_evaluation_exception_records_explicit_marker(monkeypatch):
    """Never raises out of the ranking loop, never blocks a cycle -- matches
    entry_intelligence.py's own fail-open contract -- but per Phase 3 still records an explicit
    marker rather than silently leaving the field unset."""
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate()
    confidence = _mk_confidence(80.0)

    async def _boom(**kwargs):
        raise RuntimeError("simulated failure")

    with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _boom):
        await service._apply_historical_intelligence(candidate, confidence)

    assert confidence["overall_score"] == 80.0
    hi = candidate["historical_intelligence"]
    assert hi is not None
    assert hi["status"] == "UNAVAILABLE"
    assert hi["reason"] == "HISTORICAL_INTELLIGENCE_UNAVAILABLE:RuntimeError"


@pytest.mark.asyncio
async def test_missing_strategy_id_records_explicit_marker():
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate(context={})  # no strategy_id
    confidence = _mk_confidence(80.0)
    await service._apply_historical_intelligence(candidate, confidence)
    assert confidence["overall_score"] == 80.0
    assert candidate["historical_intelligence"]["reason"] == "HIST_INTEL_CONTEXT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_mtfai1_v2_skips_hi_and_stays_neutral(monkeypatch):
    # 2026-08-25 MTFAI1 V2 confidence-calibration audit: HI's peer-group evidence has no version
    # tag separating V1-stop-normalized R units from V2's (structure-aware stop changes what "1R"
    # means), so it must stay neutral/fail-open for MTFAI1 V2 specifically until real V2
    # fingerprints/outcomes exist -- per explicit instruction, not a blanket HI disable.
    import backend.brokers.mt5.autonomous as autonomous_mod
    monkeypatch.setattr(autonomous_mod, "MT5_MTFAI1_V2_ENABLED", True)
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate(canonical_pair="EURUSD")  # context.strategy_id defaults to "mtfai1"
    confidence = _mk_confidence(80.0)

    called = False

    async def _fake_evaluate(**kwargs):
        nonlocal called
        called = True
        return {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 10.0, "defer_reject_reason": None}

    candidate["ranking_score"] = confidence["overall_score"]
    with mock.patch.object(entry_intelligence_mod, "evaluate_historical_intelligence", _fake_evaluate):
        await service._apply_historical_intelligence(candidate, confidence)

    assert called is False  # HI must never even be invoked for a V2-eligible mtfai1 candidate
    assert confidence["overall_score"] == 80.0  # unchanged
    hi = candidate["historical_intelligence"]
    assert hi["status"] == "NEUTRAL"
    assert hi["reason"] == "MTFAI1_V2_HI_NOT_YET_VERSION_COMPATIBLE"
    assert hi["ranking_adjustment"] == 0.0


@pytest.mark.asyncio
async def test_mtfai1_v2_disabled_leaves_hi_wired_normally(monkeypatch):
    import backend.brokers.mt5.autonomous as autonomous_mod
    monkeypatch.setattr(autonomous_mod, "MT5_MTFAI1_V2_ENABLED", False)
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate(canonical_pair="EURUSD")
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 7.5, "defer_reject_reason": None}

    await _run(service, candidate, confidence, evaluation)

    assert confidence["overall_score"] == pytest.approx(87.5)
    assert candidate["historical_intelligence"]["status"] == "EVALUATED"


@pytest.mark.asyncio
async def test_mtfai1_v2_neutral_gate_does_not_affect_other_strategies(monkeypatch):
    import backend.brokers.mt5.autonomous as autonomous_mod
    monkeypatch.setattr(autonomous_mod, "MT5_MTFAI1_V2_ENABLED", True)
    service = _mk_service()
    service._cycle_context_cache["EURUSD"] = SimpleNamespace()
    candidate = _mk_candidate(canonical_pair="EURUSD", context={"strategy_id": "trend_pullback", "symbol": "EURUSD"})
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 7.5, "defer_reject_reason": None}

    await _run(service, candidate, confidence, evaluation)

    assert confidence["overall_score"] == pytest.approx(87.5)
    assert candidate["historical_intelligence"]["status"] == "EVALUATED"


@pytest.mark.asyncio
async def test_mtfai1_v2_neutral_gate_does_not_affect_excluded_symbols(monkeypatch):
    # A JPY/CHF candidate would already be forced NO_TRADE upstream in _score_candidate/_screen,
    # but this confirms the gate itself is scoped correctly rather than by accident.
    import backend.brokers.mt5.autonomous as autonomous_mod
    monkeypatch.setattr(autonomous_mod, "MT5_MTFAI1_V2_ENABLED", True)
    service = _mk_service()
    service._cycle_context_cache["USDJPY"] = SimpleNamespace()
    candidate = _mk_candidate(broker_symbol="USDJPY", canonical_pair="USDJPY", context={"strategy_id": "mtfai1", "symbol": "USDJPY"})
    confidence = _mk_confidence(80.0)
    evaluation = {"status": "EVALUATED", "historical_decision": "SUPPORT", "ranking_adjustment": 7.5, "defer_reject_reason": None}

    await _run(service, candidate, confidence, evaluation)

    assert confidence["overall_score"] == pytest.approx(87.5)
    assert candidate["historical_intelligence"]["status"] == "EVALUATED"


@pytest.mark.asyncio
async def test_build_context_fallback_returns_none_on_short_history():
    """build_strategy_context's own contract: never a partial/fabricated context when there
    isn't enough history -- the fallback must respect that, not paper over it."""
    service = _mk_service()

    async def _short_candles(*a, **k):
        return []

    async def _tick(*a, **k):
        return SimpleNamespace(bid=None, ask=None, spread=None)

    import backend.brokers.mt5.autonomous as autonomous_mod

    with mock.patch.object(autonomous_mod.redis_layer, "cached_candles", _short_candles):
        with mock.patch.object(autonomous_mod.redis_layer, "cached_latest_tick", _tick):
            ctx = await service._build_context_for_historical_intelligence(_mk_candidate(context={"symbol": "EURUSD"}))
    assert ctx is None

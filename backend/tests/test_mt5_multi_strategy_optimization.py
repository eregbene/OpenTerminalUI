"""MT5 multi-strategy engine optimization + demo activation -- Part 20's 24 required tests.

Covers: deterministic optimization (shared context, no redundant analyze_bars), the
strategy-neutral cheap pre-filter, regime routing, the ACTIVE_MT5 promotion of all 10 canonical
families + MTFAI1, config-driven per-strategy disable, the demo-only operational circuit
breaker, live-trading safety guard, unified-execution-path invariants, and continued passing of
the pre-existing adaptive-management/confidence-calibration/economic-intelligence suites.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _build_multi_strategy_analysis
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.mt5_strategies import circuit_breaker
from backend.mt5_strategies.analytics import contribution_participation_report, strategy_family_performance_report, strategy_performance_report
from backend.mt5_strategies.context import cheap_prefilter, quick_regime
from backend.mt5_strategies.families import EVALUATORS, evaluate_all, evaluate_ema_trend
from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5, DISABLED, STRATEGY_FAMILIES, activation_status, all_strategy_ids, multi_strategy_enabled, regime_compatible
from backend.mt5_strategies.models import StrategySignal
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter
from backend.tests.test_mt5_multi_strategy import NOW, _flat_context, _mk_rows

COMPONENT_NAMES = (
    "trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "strategy_performance",
    "symbol_performance", "correlation_quality", "volatility_suitability", "execution_conditions", "signal_freshness",
)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker_state():
    """Circuit breaker state is deliberately in-memory/module-global (see circuit_breaker.py) --
    reset it around every test in this file so a trip in one test can never leak into another."""
    circuit_breaker.reset_all()
    yield
    circuit_breaker.reset_all()


def _analytics_candidate(candidate_id: str, *, strategy_id: str, family: str, contributing: list[str], confirmed: bool) -> dict:
    return {
        "candidate_id": candidate_id, "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG", "rank": 1,
        "trade_confidence": {
            "overall_score": 82.0, "band": "strong", "rule_version": "v1",
            "components": [{"name": n, "score": 82.0, "weight": round(1 / 9, 4), "contribution": 82.0 / 9, "reason": "t", "inputs": {}} for n in COMPONENT_NAMES],
        },
        "rejection_reasons": [],
        "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1100,
        "context": {
            "risk_reward": 2.0, "atr": 0.001, "spread": 0.0001, "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy_id": strategy_id, "strategy_family": family, "regime": "trending_up",
            "contributing_strategies": contributing, "contributing_families": [family], "multi_strategy_confirmation": confirmed,
            "conflict_state": "NONE",
        },
    }


# 1. Cycle output remains deterministic after optimization: the production analysis function
# (context-cache/regime-precompute plumbing included) yields identical strategy-derived output
# for identical inputs -- only the wall-clock timestamp fields legitimately differ.
def test_multi_strategy_analysis_deterministic_for_identical_inputs():
    rows = _mk_rows(120, base=1.1000, step=0.00006)
    instrument = SimpleNamespace(canonical_pair="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", symbol=None)
    regime_info = quick_regime(rows)
    r1 = _build_multi_strategy_analysis(instrument, "C1", rows, rows, rows, Decimal("1.1080"), Decimal("1.1082"), Decimal("0.0002"), regime_info)
    r2 = _build_multi_strategy_analysis(instrument, "C1", rows, rows, rows, Decimal("1.1080"), Decimal("1.1082"), Decimal("0.0002"), regime_info)
    regime1, smc1, candidates1, ctx1, count1 = r1
    regime2, smc2, candidates2, ctx2, count2 = r2
    assert regime1 == regime2
    assert smc1 == smc2
    assert count1 == count2
    assert len(candidates1) == len(candidates2)
    for c1, c2 in zip(candidates1, candidates2):
        assert (c1["direction"], c1["entry"], c1["stop_loss"], c1["take_profit"]) == (c2["direction"], c2["entry"], c2["stop_loss"], c2["take_profit"])
        assert c1["context"]["strategy_id"] == c2["context"]["strategy_id"]
        assert c1["context"]["contributing_strategies"] == c2["context"]["contributing_strategies"]


# 2. Shared context prevents redundant structure analysis: _entry_quality_score reuses this
# cycle's already-computed M15 SMC snapshot instead of re-running analyze_bars.
def test_entry_quality_score_reuses_cached_context_without_refetching(monkeypatch: pytest.MonkeyPatch):
    from backend.mt5_strategies.context import build_strategy_context

    service = MT5AutonomousTradingService(fake_adapter())
    rows = _mk_rows(120, base=1.1000, step=0.00006)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"), now=NOW)
    service._cycle_context_cache = {"EURUSD": ctx}

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("adapter.candles must not be called when the context cache has a hit")

    monkeypatch.setattr(service.adapter, "candles", _must_not_be_called)
    result = asyncio.run(service._entry_quality_score({"broker_symbol": "EURUSD"}))
    assert result["status"] == "ok"
    assert result["bars_analyzed"] == len(rows)


# 3. The cheap pre-filter does not depend on MTFAI1 trend conditions -- neither its signature
# nor its behavior reference direction/score/strategy identity.
def test_cheap_prefilter_is_strategy_neutral():
    params = set(inspect.signature(cheap_prefilter).parameters)
    assert not params & {"direction", "score", "strategy_id", "trend"}
    # Flat/no-net-drift data (MTFAI1's own SMA cross would say NO_TRADE here) still passes the
    # cheap filter as long as there is real intrabar range and acceptable spread.
    rows = _mk_rows(30, base=1.1000, step=0.0, wick=0.0006)
    should_analyze, reason = cheap_prefilter(already_ineligible=False, m15_rows=rows, spread=Decimal("0.0001"))
    assert should_analyze is True
    assert reason == ""


# 4. Mean-reversion setups can survive the pre-filter's regime gate.
def test_mean_reversion_regimes_survive_the_regime_gate():
    for regime in ("ranging", "low_volatility"):
        assert regime_compatible("mean_reversion", regime)
        assert any(regime_compatible(sid, regime) for sid in all_strategy_ids())


# 5. Liquidity-sweep setups can survive the pre-filter's regime gate.
def test_liquidity_sweep_regimes_survive_the_regime_gate():
    for regime in ("unstable_transition", "reversal", "ranging"):
        assert regime_compatible("liquidity_sweep_reversal", regime)
        assert any(regime_compatible(sid, regime) for sid in all_strategy_ids())


# 6. Regime routing skips incompatible strategies from the produced signal list, not just from
# execution eligibility -- trending_up never even asks mean_reversion to evaluate.
def test_regime_routing_skips_incompatible_strategy_entirely():
    ctx = _flat_context(regime="trending_up")
    signals = evaluate_all(ctx, strategy_ids=["mean_reversion", "ema_trend"])
    ids = {s.strategy_id for s in signals}
    assert "mean_reversion" not in ids
    assert "ema_trend" in ids


# 7. All 10 canonical strategies can be ACTIVE_MT5 in demo config (and default to it).
def test_all_ten_canonical_strategies_default_active_on_demo():
    # "wyckoff" (2026-08-17) is deliberately excluded from this Stage-1-complete cohort -- it
    # defaults to DISABLED pending historical/OOS validation and explicit promotion (see its own
    # STRATEGY_FAMILIES entry's comment), unlike the 10 families this test covers, which already
    # completed that evaluation period. See test_wyckoff_defaults_to_disabled below.
    non_mtfai1 = [sid for sid in STRATEGY_FAMILIES if sid not in {"mtfai1", "wyckoff"}]
    assert len(non_mtfai1) == 10
    assert all(STRATEGY_FAMILIES[sid]["default_activation"] == ACTIVE_MT5 for sid in non_mtfai1)
    assert all(activation_status(sid) == ACTIVE_MT5 for sid in non_mtfai1)


def test_wyckoff_defaults_to_disabled_pending_validation():
    assert STRATEGY_FAMILIES["wyckoff"]["default_activation"] == DISABLED
    assert activation_status("wyckoff") == DISABLED


# 8. MTFAI1 remains active.
def test_mtfai1_remains_active():
    assert activation_status("mtfai1") == ACTIVE_MT5


# 9. Research/IBKR/duplicate-deprecated strategy ids remain non-executable (never registered).
def test_research_ibkr_and_duplicate_strategies_are_not_registered():
    forbidden = {
        "xauusd_analysis_only_v1", "ema_trend_continuation_v1", "trend_pullback_v1", "breakout_retest_v1",
        "donchian_breakout_v1", "rsi_mean_reversion_v1", "mean_reversion_range_v1", "liquidity_sweep_reversal_v1",
        "smc_continuation_v1", "session_breakout_v1",
    }
    assert forbidden.isdisjoint(STRATEGY_FAMILIES.keys())


# 10 / 17. IBKR is not required by, and only centralized MT5 execution can order_send from, the
# new optimization/analytics/circuit-breaker modules.
def test_new_modules_have_no_ibkr_dependency_or_order_send():
    import backend.mt5_strategies.analytics as analytics_module
    import backend.mt5_strategies.circuit_breaker as circuit_breaker_module
    import backend.mt5_strategies.context as context_module

    for module in (analytics_module, circuit_breaker_module, context_module):
        source = inspect.getsource(module)
        assert "backend.brokers.ibkr" not in source
        assert "ibkr_config" not in source
        assert "order_send" not in source
        assert "submit_market_order" not in source


# 11. OpenAI calls remain zero end-to-end for a promoted (ACTIVE_MT5, non-mtfai1) strategy's
# winning candidate -- proves promotion never introduces an AI dependency.
def test_active_promoted_strategy_cycle_has_zero_openai_calls(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = MT5AutonomousTradingService(fake_adapter())
    active_candidate = {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "asset_class": "FOREX", "direction": "LONG",
        "ranking_score": 90.0, "rejection_reasons": [], "context_hash": "hash-active-openai",
        "context": {"risk_reward": "3.0", "atr": "0.0010", "spread": "0.0001", "timestamp": datetime.now(timezone.utc).isoformat(), "strategy_id": "ema_trend", "strategy_family": "ema_trend", "regime": "trending_up"},
        "stop_loss": "1.0950", "take_profit": "1.1150", "strategy_activation": ACTIVE_MT5,
    }
    _wire_common_cycle_mocks(monkeypatch, service, [active_candidate])
    monkeypatch.setattr(service, "_submit", lambda candidate, **kwargs: asyncio.sleep(0, result={"status": "ACCEPTED", "order_send_calls": 1}))
    result = asyncio.run(service.run_cycle(owner="active-openai-test"))
    assert result["openai_calls"] == 0
    assert result["status"] == "ACCEPTED"
    assert "SHADOW_MODE" not in result["winner"]["rejection_reasons"]


def _wire_common_cycle_mocks(monkeypatch: pytest.MonkeyPatch, service: MT5AutonomousTradingService, candidates: list[dict]) -> None:
    async def _fake_global_blockers():
        return []

    async def _fake_economic_evaluate_allow(**kwargs):
        return {"guard": {"decision": "ALLOW", "reason_codes": [], "size_multiplier": 1.0}, "calendar": None, "news": None, "macro_advisory": None}

    monkeypatch.setattr(service, "_global_blockers", lambda: _fake_global_blockers())
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=candidates))
    monkeypatch.setattr(service, "_entry_quality_score", lambda candidate: asyncio.sleep(0, result={"status": "ok", "total_score": 0.9, "positive_contributors": [], "negative_contributors": [], "trend_state": "BULLISH"}))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.confidence_memory_for_symbol", lambda symbol: (None, None))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.exposure", lambda *args, **kwargs: {"currency": {}})
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.can_open_new_trade", lambda *args, **kwargs: (True, []))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.decision_context_service.context_risk", lambda symbol: asyncio.sleep(0, result={"block_reasons": [], "acknowledgement_required": False}))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.economic_intelligence_service.evaluate_entry_deterministic", _fake_economic_evaluate_allow)


# 12. Confidence threshold remains 75.
def test_confidence_threshold_remains_75():
    assert mt5_config().min_trade_confidence == 75.0


# 13. Same-direction candidate fusion still prevents duplicate orders in the production
# analysis function (not just in fusion.py's own unit tests).
def test_build_multi_strategy_analysis_still_fuses_same_direction_agreement(monkeypatch: pytest.MonkeyPatch):
    ctx = _flat_context(regime="trending_up", htf_h4="bullish", htf_h1="bullish")
    sig_a = StrategySignal(strategy_id="ema_trend", strategy_family="ema_trend", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=80.0, proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5, regime="trending_up")
    sig_b = dataclasses.replace(sig_a, strategy_id="trend_pullback", strategy_family="trend_pullback", raw_signal_strength=75.0)
    monkeypatch.setattr("backend.brokers.mt5.autonomous.evaluate_all", lambda ctx, **kwargs: [sig_a, sig_b])
    instrument = SimpleNamespace(canonical_pair="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", symbol=None)
    regime, smc_evidence, candidates, built_ctx, count = _build_multi_strategy_analysis(instrument, "C13", ctx.m15_rows, ctx.h1_rows, ctx.h4_rows, ctx.bid, ctx.ask, ctx.spread, None)
    assert len(candidates) == 1
    assert set(candidates[0]["context"]["contributing_strategies"]) == {"ema_trend", "trend_pullback"}
    assert count == 2


# 14. Opposite-direction conflict resolution remains deterministic across repeated calls.
def test_conflict_resolution_deterministic_across_repeated_calls():
    long_sig = StrategySignal(strategy_id="ema_trend", strategy_family="ema_trend", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=90.0, proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5, regime="trending_up")
    short_sig = dataclasses.replace(long_sig, strategy_id="mean_reversion", strategy_family="mean_reversion", direction="SHORT", raw_signal_strength=60.0, stop_loss=1.1030, take_profit=1.0960)
    r1 = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C14", signals=[long_sig, short_sig], htf_trend_h4="bullish", now=NOW)
    r2 = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C14", signals=[long_sig, short_sig], htf_trend_h4="bullish", now=NOW)
    assert len(r1) == len(r2) == 1
    assert r1[0]["direction"] == r2[0]["direction"] == "LONG"
    assert r1[0]["context"]["conflict_state"] == r2[0]["context"]["conflict_state"] == "RESOLVED_DOMINANT_STRENGTH"


# 15. Promoted (ACTIVE_MT5) strategies still pass through the economic-risk guard.
def test_active_promoted_strategy_still_blocked_by_economic_guard(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = MT5AutonomousTradingService(fake_adapter())
    active_candidate = {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "asset_class": "FOREX", "direction": "LONG",
        "ranking_score": 90.0, "rejection_reasons": [], "context_hash": "hash-active-econ",
        "context": {"risk_reward": "3.0", "atr": "0.0010", "spread": "0.0001", "timestamp": datetime.now(timezone.utc).isoformat(), "strategy_id": "ema_trend", "strategy_family": "ema_trend", "regime": "trending_up"},
        "stop_loss": "1.0950", "take_profit": "1.1150", "strategy_activation": ACTIVE_MT5,
    }
    _wire_common_cycle_mocks(monkeypatch, service, [active_candidate])

    async def _fake_economic_evaluate_block(**kwargs):
        return {"guard": {"decision": "BLOCK", "reason_codes": ["HIGH_IMPACT_NEWS"], "size_multiplier": 0.0}, "calendar": None, "news": None, "macro_advisory": None}

    monkeypatch.setattr("backend.brokers.mt5.autonomous.economic_intelligence_service.evaluate_entry_deterministic", _fake_economic_evaluate_block)
    result = asyncio.run(service.run_cycle(owner="econ-guard-test"))
    assert result["status"] == "SKIPPED_ECONOMIC_RISK"


# 16. Promoted (ACTIVE_MT5) strategies still pass through the portfolio-risk guard.
def test_active_promoted_strategy_still_blocked_by_portfolio_guard(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = MT5AutonomousTradingService(fake_adapter())
    active_candidate = {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "asset_class": "FOREX", "direction": "LONG",
        "ranking_score": 90.0, "rejection_reasons": [], "context_hash": "hash-active-portfolio",
        "context": {"risk_reward": "3.0", "atr": "0.0010", "spread": "0.0001", "timestamp": datetime.now(timezone.utc).isoformat(), "strategy_id": "ema_trend", "strategy_family": "ema_trend", "regime": "trending_up"},
        "stop_loss": "1.0950", "take_profit": "1.1150", "strategy_activation": ACTIVE_MT5,
    }
    _wire_common_cycle_mocks(monkeypatch, service, [active_candidate])
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.can_open_new_trade", lambda *args, **kwargs: (False, ["MAX_TOTAL_OPEN_RISK"]))
    result = asyncio.run(service.run_cycle(owner="portfolio-guard-test"))
    assert result["status"] == "PORTFOLIO_REJECTED"


# 18. Live trading remains blocked, with an explicit hard guard in _submit as defense-in-depth
# on top of the pre-existing config default.
def test_live_trading_remains_blocked_by_config_and_submit_guard(monkeypatch: pytest.MonkeyPatch):
    assert mt5_config().live_trading_enabled is False
    service = MT5AutonomousTradingService(fake_adapter())
    monkeypatch.setattr(service, "config", dataclasses.replace(service.config, live_trading_enabled=True))

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("_submit must reject before touching the adapter when live_trading_enabled is true")

    monkeypatch.setattr(service.adapter, "mt5_account", _must_not_be_called)
    result = asyncio.run(service._submit({"broker_symbol": "EURUSD", "direction": "LONG"}))
    assert result["status"] == "REJECTED"
    assert "LIVE_TRADING_BLOCKED" in result["reasons"]
    assert result["order_send_calls"] == 0


# 19. Per-strategy disable works immediately via config, without a code deployment.
def test_strategy_disabled_via_env_is_skipped_entirely(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_EMA_TREND", "DISABLED")
    assert activation_status("ema_trend") == DISABLED
    ctx = _flat_context(regime="trending_up")
    signals = evaluate_all(ctx, strategy_ids=["ema_trend"])
    assert len(signals) == 1
    assert signals[0].valid is False
    assert signals[0].rejection_reason == "STRATEGY_DISABLED"


# 20. The malformed-signal circuit breaker trips on repeated malformation and never lets a
# tripped strategy contribute a signal (and therefore never an order) again.
def test_malformed_signal_trips_circuit_breaker_and_blocks_further_evaluation(monkeypatch: pytest.MonkeyPatch):
    ctx = _flat_context(regime="trending_up")
    # Geometrically impossible for a LONG: stop ABOVE entry.
    malformed = StrategySignal(strategy_id="ema_trend", strategy_family="ema_trend", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=80.0, proposed_entry=1.1000, stop_loss=1.1050, take_profit=1.1100, reward_risk=2.0, regime="trending_up")
    assert circuit_breaker.validate_signal_sanity(malformed) == "MALFORMED_GEOMETRY"
    monkeypatch.setitem(EVALUATORS, "ema_trend", lambda ctx: malformed)
    for _ in range(circuit_breaker.MALFORMED_SIGNAL_TRIP_THRESHOLD):
        evaluate_all(ctx, strategy_ids=["ema_trend"])
    assert circuit_breaker.is_tripped("ema_trend") is True
    assert activation_status("ema_trend") == DISABLED
    signals = evaluate_all(ctx, strategy_ids=["ema_trend"])
    assert signals[0].rejection_reason == "CIRCUIT_BREAKER_OPEN"
    assert signals[0].valid is False


# 21. Calibration/analytics records active-strategy identity and never double-counts a fused
# trade under its contributing strategies.
def test_strategy_analytics_groups_by_anchor_without_double_counting(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    fused = _analytics_candidate("C21a:EURUSD:x", strategy_id="ema_trend", family="ema_trend", contributing=["ema_trend", "trend_pullback"], confirmed=True)
    rejected = _analytics_candidate("C21c:EURUSD:z", strategy_id="ema_trend", family="ema_trend", contributing=["ema_trend"], confirmed=False)
    rejected["rejection_reasons"] = ["PORTFOLIO_RISK_LIMIT"]
    capture_cycle_candidate_evaluations({"cycle_id": "C21a", "status": "NO_TRADE", "candidates": [fused], "winner": fused, "trade": None, "openai_calls": 0, "order_send_calls": 0})
    capture_cycle_candidate_evaluations({"cycle_id": "C21c", "status": "PORTFOLIO_REJECTED", "candidates": [rejected], "winner": rejected, "trade": None, "openai_calls": 0, "order_send_calls": 0})
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C21a:EURUSD:x").one()
        row.outcome_type = "EXECUTED"
        row.outcome_status = "TP_HIT"
        row.realized_r = 1.5
        row.tp_hit = True
        db.commit()

    report = strategy_performance_report()
    assert report["ema_trend"]["candidates_evaluated"] == 2
    assert report["ema_trend"]["actual_trades"] == 1
    assert report["ema_trend"]["wins"] == 1
    assert report["ema_trend"]["portfolio_rejection_rate"] == 0.5
    assert set(report["ema_trend"]["contributing_strategies_seen"]) == {"ema_trend", "trend_pullback"}
    # trend_pullback never ANCHORS a candidate here -- it must not show independent trade credit.
    assert report.get("trend_pullback", {}).get("actual_trades", 0) == 0

    family_report = strategy_family_performance_report()
    assert family_report["ema_trend"]["actual_trades"] == 1

    participation = contribution_participation_report()
    assert participation["trend_pullback"]["candidates_participated_in"] == 1
    assert participation["trend_pullback"]["resolved_wins_participated_in"] == 1


# 22. Existing adaptive-management tests still pass (smoke check; the full suite is run as part
# of this task's regression pass).
def test_adaptive_management_service_still_importable_and_functional():
    from backend.adaptive_management.service import AdaptiveManagementService, ManagementCandidate

    service = AdaptiveManagementService()
    candidates = [ManagementCandidate(action_type="HOLD", priority=100), ManagementCandidate(action_type="TRAIL_STOP", priority=5)]
    assert service._select_action(candidates).action_type == "TRAIL_STOP"


# 23. Existing confidence-calibration tests still pass (smoke check).
def test_confidence_calibration_reports_still_callable(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    from backend.brokers.mt5.confidence_calibration import calibration_reliability_report, confidence_band_report

    assert isinstance(confidence_band_report(), dict)
    assert isinstance(calibration_reliability_report(), dict)


# 24. Existing economic-intelligence tests still pass (smoke check).
def test_economic_intelligence_classification_still_callable():
    from backend.economic_intelligence.event_mapping import is_central_bank_event

    assert is_central_bank_event("FOMC Rate Decision") is True
    assert is_central_bank_event("Cleveland Fed Inflation Expectations") is False


# --- Bonus coverage beyond the required 24: the global multi-strategy kill switch (Part 16). ---
def test_multi_strategy_master_switch_defaults_enabled_and_can_be_disabled(monkeypatch: pytest.MonkeyPatch):
    assert multi_strategy_enabled() is True
    monkeypatch.setenv("MT5_MULTI_STRATEGY_ENABLED", "false")
    assert multi_strategy_enabled() is False

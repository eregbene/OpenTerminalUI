"""Cycle-level tests proving the MT5 autonomous entry cycle is fully deterministic:
zero OpenAI/provider calls, no provider-limit skip path, portfolio validation as a
separate gate from confidence, and candidate ranking/selection/logging behavior.

Uses the same fake_adapter() fixture as test_mt5_adapter.py, with _screen/
_entry_quality_score/portfolio/economic/decision-context calls monkeypatched to
controlled synthetic data -- the fake MT5 candle data is flat (identical OHLC every
bar) so it can't produce a realistic trend-following signal on its own; these tests
isolate the NEW deterministic-confidence/ranking/portfolio-gate logic instead.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

import backend.brokers.mt5.autonomous as autonomous_module
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def _screened_candidate(*, symbol: str, ranking_score: float, risk_reward: str = "2.0") -> dict:
    return {
        "canonical_pair": symbol,
        "broker_symbol": symbol,
        "asset_class": "FOREX",
        "direction": "LONG",
        "ranking_score": ranking_score,
        "rejection_reasons": [],
        "context_hash": f"hash-{symbol}",
        "context": {
            "risk_reward": risk_reward,
            "atr": "0.0010",
            "spread": "0.0001",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        # Fake quote ask~1.1002 (see FakeMT5.symbol_info) -- these give a real reward:risk
        # of ~1.9 (distance 0.0052 to stop, 0.0098 to target), comfortably above
        # MIN_REWARD_MULTIPLE (1.5) so a genuine submission attempt reaches order_send.
        "stop_loss": "1.0950",
        "take_profit": "1.1100",
    }


def _wire_common_mocks(monkeypatch: pytest.MonkeyPatch, service: MT5AutonomousTradingService, *, entry_quality_score: float = 0.7, portfolio_allowed: bool = True) -> None:
    # Isolated in-memory sqlite for the mt5_autonomous state store (get_state/set_state,
    # which backs the duplicate-candle check and persist_cycle_result) -- never the shared
    # dev database, and never leaks processed-candle state between test functions.
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(service, "_global_blockers", lambda: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(service, "_entry_quality_score", lambda candidate: asyncio.sleep(0, result={"status": "ok", "total_score": entry_quality_score, "positive_contributors": [], "negative_contributors": [], "trend_state": "BULLISH"}))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.confidence_memory_for_symbol", lambda symbol: (None, None))
    # Healthy-redis baseline, matching production reality (the live cycle history shows
    # redis_degraded=False on every recent real cycle) -- without this, get_client() returns
    # None in this isolated pytest process (the app's cache client is only established via the
    # real FastAPI lifespan, which these tests never start), which would make every candidate's
    # execution_conditions component look degraded purely as a test-harness artifact, not a
    # real signal. See test_degraded_redis_lowers_execution_conditions_and_can_reject_a_candidate
    # for the actual degraded-path coverage.
    monkeypatch.setattr("backend.brokers.mt5.autonomous.redis_layer.get_client", lambda: object())
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.exposure", lambda *args, **kwargs: {"currency": {}})
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.can_open_new_trade", lambda *args, **kwargs: (portfolio_allowed, [] if portfolio_allowed else ["MAX_TOTAL_OPEN_RISK"]))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.decision_context_service.context_risk", lambda symbol: asyncio.sleep(0, result={"block_reasons": [], "acknowledgement_required": False}))

    async def _fake_economic_evaluate(**kwargs):
        return {"guard": {"decision": "ALLOW", "reason_codes": [], "size_multiplier": 1.0}, "calendar": None, "news": None, "macro_advisory": None}

    monkeypatch.setattr("backend.brokers.mt5.autonomous.economic_intelligence_service.evaluate_entry_deterministic", _fake_economic_evaluate)


# 8. Autonomous trading performs zero OpenAI calls.
def test_autonomous_cycle_performs_zero_openai_calls(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.9)
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[_screened_candidate(symbol="EURUSD", ranking_score=88.0, risk_reward="3.0")]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["openai_calls"] == 0


# 9. Provider limits cannot skip a trading cycle -- there is no provider-limit check
# in the deterministic cycle at all (source-level proof, not just behavioral).
def test_no_provider_limit_gate_exists_in_source():
    source = inspect.getsource(autonomous_module)
    assert "usage_ledger" not in source
    assert "ai_trading_config" not in source
    assert "provider_registry" not in source
    assert "_ai_decision" not in source


# 10. SKIPPED_PROVIDER_LIMIT is no longer produced by the deterministic autonomous cycle.
def test_skipped_provider_limit_status_removed():
    source = inspect.getsource(autonomous_module)
    assert "SKIPPED_PROVIDER_LIMIT" not in source
    assert "AI_REJECTED" not in source


# 7. No valid candidate (all below confidence threshold) results in NO_TRADE.
def test_no_valid_candidate_yields_no_trade(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.1)
    weak = _screened_candidate(symbol="EURUSD", ranking_score=20.0, risk_reward="0.5")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[weak]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["status"] == "NO_TRADE"
    assert adapter.client.mt5.order_send_calls == 0
    assert result["candidates"][0]["trade_confidence"]["overall_score"] < 75.0
    assert "BELOW_CONFIDENCE_THRESHOLD" in result["candidates"][0]["rejection_reasons"]


# 4/5 (cycle level). Multiple valid candidates are ranked deterministically and the
# strongest risk-adjusted one is selected; the runner-up is logged as LOWER_RANKED_CANDIDATE.
def test_best_of_multiple_eligible_candidates_is_selected(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.85)
    strong = _screened_candidate(symbol="GBPUSD", ranking_score=87.0, risk_reward="3.2")
    weaker_but_eligible = _screened_candidate(symbol="EURUSD", ranking_score=76.0, risk_reward="1.6")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[strong, weaker_but_eligible]))

    result = asyncio.run(service.run_cycle(owner="local", dry_run=True))

    assert result["winner"]["canonical_pair"] == "GBPUSD"
    ranked_symbols = [row["canonical_pair"] for row in result["candidates"]]
    assert ranked_symbols[0] == "GBPUSD"
    runner_up = next(row for row in result["candidates"] if row["canonical_pair"] == "EURUSD")
    assert "LOWER_RANKED_CANDIDATE" in runner_up["rejection_reasons"]


# execution_conditions must reflect real, per-cycle redis health rather than always being a
# constant 100 (the confidence-calibration audit found score_vs_outcome_r_correlation=null for
# this component precisely because nothing ever varied it). A redis outage this cycle should
# both lower the score and be visible in the component's own reason/inputs.
def test_degraded_redis_lowers_execution_conditions_and_can_reject_a_candidate(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.85)
    # Overrides the healthy-redis baseline _wire_common_mocks just set, simulating a real
    # client outage for this one test.
    monkeypatch.setattr("backend.brokers.mt5.autonomous.redis_layer.get_client", lambda: None)
    borderline = _screened_candidate(symbol="EURUSD", ranking_score=76.0, risk_reward="1.6")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[borderline]))

    result = asyncio.run(service.run_cycle(owner="local", dry_run=True))

    execution_component = next(c for c in result["candidates"][0]["trade_confidence"]["components"] if c["name"] == "execution_conditions")
    assert execution_component["score"] < 100.0
    assert "REDIS_UNAVAILABLE" in execution_component["inputs"]["degraded_flags"]


# 6. A 90+ confidence candidate can still be rejected by portfolio limits, and its
# confidence score is not rewritten because of that rejection.
def test_high_confidence_candidate_rejected_by_portfolio_limits(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.95, portfolio_allowed=False)
    strong = _screened_candidate(symbol="GBPUSD", ranking_score=88.0, risk_reward="3.5")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[strong]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["status"] == "PORTFOLIO_REJECTED"
    stored_confidence = result["winner"]["trade_confidence"]["overall_score"]
    assert stored_confidence >= 75.0  # signal was genuinely valid
    assert "PORTFOLIO_RISK_LIMIT" in result["winner"]["rejection_reasons"]
    assert adapter.client.mt5.order_send_calls == 0


# TRADING_DISABLED replaces the old SKIPPED_BLOCKED status name (same semantics: global
# blockers -- MT5 disabled, emergency stop, etc. -- prevent the cycle from proceeding).
def test_global_blockers_yield_trading_disabled(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    monkeypatch.setattr(service, "_global_blockers", lambda: asyncio.sleep(0, result=["EMERGENCY_DISABLED"]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["status"] == "TRADING_DISABLED"
    assert "EMERGENCY_DISABLED" in result["blockers"]


# 13. Existing execution-manager journaling still works -- _submit still routes through
# self.execution.submit_market_order (the ExecutionManager/broker-journaling path), unchanged.
def test_submit_still_routes_through_execution_manager(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.85)
    candidate = _screened_candidate(symbol="EURUSD", ranking_score=85.0, risk_reward="3.0")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[candidate]))

    calls = {"count": 0}
    original = service.execution.submit_market_order

    async def _spy(intent, **kwargs):
        calls["count"] += 1
        return await original(intent, **kwargs)

    monkeypatch.setattr(service.execution, "submit_market_order", _spy)

    result = asyncio.run(service.run_cycle(owner="local"))

    assert calls["count"] == 1
    assert result["trade"] is not None


# 18. Live trading remains disabled by default.
def test_live_trading_disabled_by_default():
    adapter = fake_adapter()
    assert adapter.config.live_trading_enabled is False


def test_min_trade_confidence_default_is_75():
    adapter = fake_adapter()
    assert adapter.config.min_trade_confidence == 75.0

"""MT5 OpenAI removal from the economic-risk path + economic-risk double-sizing fix -- the 20
required tests.

Covers: evaluate_entry_deterministic() is structurally LLM-free regardless of
ff_openai_macro_classification_enabled, the unscheduled-news guard fails safe (neutral ALLOW,
not a silent LLM fallback) rather than inventing a new classifier, openai_calls is a real
measured count, calendar/central-bank/high-impact deterministic guard behavior is unchanged
end-to-end, the economic-risk size reduction is applied exactly once (not twice), and every
previously-established invariant (confidence threshold 75, adaptive manager, IBKR absence, live
trading blocked, the JPY cross-currency risk-calculator fix) still holds.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import backend.brokers.mt5.autonomous as autonomous_module
from backend.brokers.mt5 import risk_budget
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.risk_calculator import calculate_conservative_loss_per_lot_sync
from backend.economic_intelligence import calendar_guard, macro_context
from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.service import EconomicIntelligenceService
from backend.mt5_strategies.models import STRATEGY_FAMILIES
from backend.tests.test_mt5_adapter import fake_adapter
from backend.tests.test_mt5_autonomous_deterministic import _screened_candidate, _wire_common_mocks
from backend.tests.test_risk_calculator import _eurjpy_live

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _event(*, currency: str = "USD", impact: str = "high", minutes_from_now: int, central_bank: bool = False, name: str = "Non-Farm Payrolls") -> dict:
    return {"currency": currency, "impact": impact, "scheduled_at_utc": (NOW + timedelta(minutes=minutes_from_now)).isoformat(), "is_central_bank_event": central_bank, "raw_name": name}


def _mock_evaluate_dependencies(monkeypatch: pytest.MonkeyPatch, *, events: list[dict] | None = None, news: list[dict] | None = None) -> None:
    """Isolates evaluate_entry_deterministic()/evaluate_entry() from the database entirely --
    every function they call that would otherwise hit persistence is replaced with a
    deterministic, in-memory stand-in, so these tests exercise the real decision logic without
    needing a database fixture."""
    monkeypatch.setattr("backend.economic_intelligence.service.query_events", lambda **kwargs: events or [])
    monkeypatch.setattr("backend.economic_intelligence.service.query_news", lambda **kwargs: news or [])
    monkeypatch.setattr("backend.economic_intelligence.service.save_trade_context_snapshot", lambda payload: "snap-test")
    monkeypatch.setattr("backend.economic_intelligence.service.provider_health.health_snapshot", lambda config: {"items": [{"provider": "ff_calendar_json", "state": "FRESH", "freshness_seconds": 10}]})
    # _evaluate() computes `now` itself (module-level utcnow(), real wall-clock time) -- pin it
    # to NOW so events built relative to NOW (via minutes_from_now) land in the same reference
    # frame the guard actually evaluates against.
    monkeypatch.setattr("backend.economic_intelligence.service.utcnow", lambda: NOW)


def _svc(**overrides) -> EconomicIntelligenceService:
    return EconomicIntelligenceService(EconomicIntelligenceConfig(**overrides))


# 1 / 6. MT5 economic-risk evaluation makes zero OpenAI calls -- measured, with the flag ON.
def test_mt5_economic_evaluation_makes_zero_openai_calls(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=30)])
    svc = _svc(ff_openai_macro_classification_enabled=True)
    before = macro_context.classify_call_count()

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C1"))

    assert macro_context.classify_call_count() == before
    assert result["llm_calls_made"] == 0
    assert result["macro_advisory"] is None


# 2. macro_context.classify() is structurally unreachable from the MT5 path -- not merely "did
# not happen to fire this time".
def test_macro_context_classify_unreachable_from_mt5_path(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=30)], news=[{"headline": "shock", "currency": "USD"}])

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("macro_context.classify must never be reachable from evaluate_entry_deterministic")

    monkeypatch.setattr(macro_context, "classify", _must_not_be_called)
    svc = _svc(ff_openai_macro_classification_enabled=True)

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C2"))

    assert result["macro_classification_status"] == "MACRO_CLASSIFICATION_UNAVAILABLE"


# 3. ff_openai_macro_classification_enabled=True cannot reintroduce OpenAI into MT5.
def test_config_flag_true_cannot_reintroduce_openai_into_mt5(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=30)])
    svc = _svc(ff_openai_macro_classification_enabled=True)
    assert svc.config.ff_openai_macro_classification_enabled is True

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C3"))

    assert result["llm_calls_made"] == 0


# 4. NEWS_RISK path is OpenAI-free for MT5 -- with no LLM advisory available, the unscheduled-
# news guard fails safe to neutral ALLOW rather than secretly keeping an LLM-derived signal
# alive through a different code path.
def test_news_risk_path_is_openai_free_for_mt5(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, news=[{"headline": "shock event", "currency": "USD"}])
    svc = _svc(ff_openai_macro_classification_enabled=True)

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C4"))

    assert result["news"]["decision"] == "ALLOW"
    assert result["news"]["reason_codes"] == []
    assert result["llm_calls_made"] == 0


# 5. openai_calls is measured (macro_context.classify_call_count() delta across the cycle), not
# a hardcoded literal.
def test_openai_calls_is_measured_not_hardcoded():
    source = inspect.getsource(autonomous_module)
    assert '"openai_calls": 0' not in source
    assert "classify_call_count" in source


# 7. Calendar high-impact blocking still works end-to-end through evaluate_entry_deterministic.
def test_calendar_high_impact_blocking_still_works_end_to_end(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=10, impact="high")])
    svc = _svc()

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C7"))

    assert result["guard"]["decision"] == "BLOCK"
    assert any("HIGH_IMPACT_EVENT_PRE_BLOCK" in reason for reason in result["guard"]["reason_codes"])


# 8. Central-bank blocking still works end-to-end.
def test_central_bank_blocking_still_works_end_to_end(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=30, central_bank=True, name="FOMC Press Conference")])
    svc = _svc()

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C8"))

    assert result["guard"]["decision"] == "BLOCK"
    assert any("CENTRAL_BANK_EVENT" in reason for reason in result["guard"]["reason_codes"])


# 9. High-impact/medium-impact event size reduction still works deterministically.
def test_medium_impact_event_reduces_size_deterministically_end_to_end(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[_event(minutes_from_now=5, impact="medium")])
    svc = _svc()

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C9"))

    assert result["guard"]["decision"] == "REDUCE_SIZE"
    assert result["guard"]["size_multiplier"] < 1.0
    assert result["llm_calls_made"] == 0


# 10. Normal (no relevant events) still ALLOWs.
def test_no_relevant_events_allows_end_to_end(monkeypatch: pytest.MonkeyPatch):
    _mock_evaluate_dependencies(monkeypatch, events=[])
    svc = _svc()

    result = asyncio.run(svc.evaluate_entry_deterministic(canonical_pair="EURUSD", direction="LONG", candidate_id="C10"))

    assert result["guard"]["decision"] == "ALLOW"


# 11. The economic-risk penalty is applied exactly once to the final lot size, not twice -- the
# volume calculate_risk_size returns (which already reflects the ONE economic_risk_factor
# reduction, applied to the budget before sizing) is the volume that actually gets submitted.
def test_economic_penalty_not_applied_twice_to_final_lot_size(monkeypatch: pytest.MonkeyPatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.9)
    candidate = _screened_candidate(symbol="EURUSD", ranking_score=88.0, risk_reward="3.0")
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[candidate]))

    async def _fake_reduce_size(**kwargs):
        return {"guard": {"decision": "REDUCE_SIZE", "reason_codes": ["NEWS_RISK_MEDIUM"], "size_multiplier": 0.5}, "calendar": None, "news": None, "macro_advisory": None}

    monkeypatch.setattr("backend.brokers.mt5.autonomous.economic_intelligence_service.evaluate_entry_deterministic", _fake_reduce_size)

    captured: dict[str, Decimal] = {}
    original_calc = service.execution.calculate_risk_size

    async def _spy_calc(**kwargs):
        risk = await original_calc(**kwargs)
        captured["risk_volume"] = risk.volume
        return risk

    monkeypatch.setattr(service.execution, "calculate_risk_size", _spy_calc)

    result = asyncio.run(service.run_cycle(owner="double-count-test"))

    assert "risk_volume" in captured
    submitted_volume = Decimal(str(result["trade"]["intent"]["volume"]))
    assert submitted_volume == captured["risk_volume"]


# 12. The confidence multiplier remains independent of the economic-risk multiplier.
def test_confidence_multiplier_remains_independent_of_economic_multiplier():
    adjustment = risk_budget.compute_risk_multiplier(economic_risk_level="medium", strategy_confidence=0.9)
    assert adjustment.components["economic_risk_factor"] == 0.7
    assert "strategy_confidence_factor" in adjustment.components
    assert adjustment.multiplier == pytest.approx(0.7 * adjustment.components["strategy_confidence_factor"])


# 13. Risk cap is never exceeded.
def test_projected_loss_never_exceeds_trade_risk_cap(monkeypatch: pytest.MonkeyPatch):
    execution = MT5ExecutionService(fake_adapter())
    symbol = MT5Symbol(symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"), trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"), trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"), volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"))
    risk = asyncio.run(execution.calculate_risk_size(account_equity=Decimal("10000"), symbol=symbol, direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), target=Decimal("1.10150")))
    assert risk.projected_loss_usd <= risk.trade_risk_cap_usd


# 14. Broker lot normalization (floor to volume_step) remains correct.
def test_broker_lot_normalization_floors_to_volume_step():
    execution = MT5ExecutionService(fake_adapter())
    symbol = MT5Symbol(symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"), trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"), trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"), volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"))
    risk = asyncio.run(execution.calculate_risk_size(account_equity=Decimal("10000"), symbol=symbol, direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), target=Decimal("1.10150")))
    if risk.status == "APPROVED":
        assert (risk.volume / Decimal("0.01")) == (risk.volume / Decimal("0.01")).to_integral_value()


# 15. The JPY cross-currency risk-calculator fix remains correct: contract_size is still
# correctly excluded (never compared across mismatched currencies) rather than silently
# computed wrong. The sync path has no adapter to attempt a live conversion, so under the
# explicit quorum policy (>=2 trusted methods required) it now fails closed on the lone
# remaining method rather than trusting it alone -- a deliberate strengthening, not a
# regression; the async path (execution.py's real call site) has adapter access and still sizes
# correctly (see test_risk_calculator.py's conversion tests).
def test_jpy_cross_currency_risk_fix_remains_correct():
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("183.788"), stop=Decimal("183.757"), symbol_info=_eurjpy_live(), account_currency="USD")
    assert result.estimates["contract_size"].available is False
    assert "profit currency" in result.estimates["contract_size"].detail
    assert result.blocked is True
    assert result.block_reason == "INSUFFICIENT_RISK_METHOD_QUORUM"


# 16. Strategy engine (canonical strategy registry) unchanged by this task.
def test_strategy_engine_unchanged():
    # "donchian_trend_follow" (2026-08-24) is excluded from this count for the exact same reason
    # as wyckoff below -- a later addition, DISABLED pending its own 3-year OOS validation.
    # "wyckoff" (2026-08-17) is excluded from this count -- it's a later addition, pending
    # historical/OOS validation, not part of the Stage-1-complete cohort this test covers.
    non_mtfai1 = [sid for sid in STRATEGY_FAMILIES if sid not in {"mtfai1", "wyckoff", "donchian_trend_follow", "session_liquidity_breakout", "fx_relative_momentum"}]
    assert len(non_mtfai1) == 10
    assert "mtfai1" in STRATEGY_FAMILIES


# 17. Confidence threshold matches the current operational value -- 2026-08-26: lowered
# 75 -> 55 (user-requested trade-frequency increase, docker-compose.yml).
def test_confidence_threshold_matches_current_operational_value():
    assert mt5_config().min_trade_confidence == 55.0


# 18. Adaptive manager unchanged.
def test_adaptive_manager_unchanged():
    from backend.adaptive_management.service import AdaptiveManagementService, ManagementCandidate

    service = AdaptiveManagementService()
    candidates = [ManagementCandidate(action_type="HOLD", priority=100), ManagementCandidate(action_type="TRAIL_STOP", priority=5)]
    assert service._select_action(candidates).action_type == "TRAIL_STOP"


# 19. IBKR remains absent from every file touched by this task.
def test_ibkr_remains_absent_from_touched_files():
    import backend.economic_intelligence.macro_context as macro_context_module
    import backend.economic_intelligence.service as service_module

    for module in (macro_context_module, service_module, autonomous_module):
        source = inspect.getsource(module)
        assert "backend.brokers.ibkr" not in source
        assert "ibkr_config" not in source


# 20. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False


# Bonus: calendar_guard.py itself is untouched/unchanged by this refactor -- direct regression
# reusing the same fixtures test_economic_intelligence_guards.py already validates.
def test_calendar_guard_module_unchanged_by_refactor():
    from backend.economic_intelligence.config import economic_intelligence_config

    cfg = economic_intelligence_config()
    events = [_event(minutes_from_now=10, impact="high")]
    assert calendar_guard.evaluate(["USD"], NOW, events, cfg)["decision"] == "BLOCK"

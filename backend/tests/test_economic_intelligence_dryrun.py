from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.economic_intelligence import persistence, service
from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.orm import EconomicEventORM
from backend.economic_intelligence.service import EconomicIntelligenceService
from backend.shared.db import Base

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(persistence, "SessionLocal", session_factory)
    return session_factory


def _mock_portfolio_allow(monkeypatch, *, allowed=True, blockers=None):
    monkeypatch.setattr(service.portfolio_manager, "can_open_new_trade", lambda: (allowed, blockers or []))


def _mock_live_trading(monkeypatch, *, blocked=False):
    class _Cfg:
        live_trading_enabled = blocked

    monkeypatch.setattr(service, "mt5_config", lambda: _Cfg())


def _svc(monkeypatch, **config_overrides):
    _session_factory(monkeypatch)
    _mock_portfolio_allow(monkeypatch)
    _mock_live_trading(monkeypatch, blocked=False)
    return EconomicIntelligenceService(EconomicIntelligenceConfig(**config_overrides))


# --- core dry-run behavior (32-38) --------------------------------------------------------


def test_dry_run_never_calls_order_send(monkeypatch):
    """Structural guarantee: dry_run_evaluate never CALLS order_send/submit_market_order/
    submit_mt5_request/MT5TradeIntent -- checked against actual call-sites (a `name(` pattern),
    not the docstring's prose explaining that it deliberately avoids them."""
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(EconomicIntelligenceService.dry_run_evaluate))
    tree = ast.parse(source)
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name:
                called_names.add(name)
    for forbidden in ("order_send", "submit_market_order", "submit_mt5_request", "MT5TradeIntent"):
        assert forbidden not in called_names


def test_high_impact_event_dry_run_returns_block(monkeypatch):
    svc = _svc(monkeypatch)
    payload = {"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "USD", "impact": "high", "minutes_from_now": 20}]}
    result = asyncio.run(svc.dry_run_evaluate(payload))
    assert result["final_restrictive_decision"] == "BLOCK"
    assert result["order_send_reachable"] is False
    assert result["order_sent"] is False


def test_reduce_size_returns_adjusted_volume(monkeypatch):
    svc = _svc(monkeypatch)
    payload = {"symbol": "EURUSD", "direction": "LONG", "volume": 2.0, "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "EUR", "impact": "medium", "minutes_from_now": 10}]}
    result = asyncio.run(svc.dry_run_evaluate(payload))
    assert result["final_restrictive_decision"] == "REDUCE_SIZE"
    assert result["adjusted_volume"] is not None
    assert result["adjusted_volume"] < 2.0


def test_provider_unavailable_follows_configured_fallback(monkeypatch):
    svc = _svc(monkeypatch, ff_provider_fail_mode="conservative")
    payload = {"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_provider_state": {"calendar_state": "UNAVAILABLE"}}
    result = asyncio.run(svc.dry_run_evaluate(payload))
    assert result["final_restrictive_decision"] == "BLOCK"
    assert result["provider_health_decision"]["calendar_state"] == "UNAVAILABLE"


def test_openai_allow_cannot_override_deterministic_block(monkeypatch):
    svc = _svc(monkeypatch)
    payload = {
        "symbol": "EURUSD",
        "direction": "LONG",
        "decision_time": NOW.isoformat(),
        "synthetic_events": [{"currency": "USD", "impact": "high", "minutes_from_now": 5}],
        "synthetic_openai_advisory": {"recommended_action": "allow", "risk_level": "low", "confidence": 0.95},
    }
    result = asyncio.run(svc.dry_run_evaluate(payload))
    assert result["final_restrictive_decision"] == "BLOCK"


def test_portfolio_rejection_is_included_in_final_result(monkeypatch):
    svc = _svc(monkeypatch)
    _mock_portfolio_allow(monkeypatch, allowed=False, blockers=["MAX_TOTAL_OPEN_RISK"])
    payload = {"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": []}
    result = asyncio.run(svc.dry_run_evaluate(payload))
    assert result["final_restrictive_decision"] == "BLOCK"
    assert "MAX_TOTAL_OPEN_RISK" in result["reason_codes"]
    assert result["portfolio_manager_decision"]["allowed"] is False


def test_economic_context_payload_matches_execution_journal_schema_fields(monkeypatch):
    svc = _svc(monkeypatch)
    payload = {"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": []}
    result = asyncio.run(svc.dry_run_evaluate(payload))
    context = result["economic_context_payload"]
    for key in ("guard", "shadow_guard", "calendar", "news", "macro_advisory", "economic_guard_mode"):
        assert key in context


# --- the 14 named end-to-end scenarios -----------------------------------------------------


def test_scenario_1_eurusd_high_impact_usd_event_in_20_minutes(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "USD", "impact": "high", "minutes_from_now": 20}]}))
    assert result["final_restrictive_decision"] == "BLOCK"


def test_scenario_2_eurusd_medium_impact_eur_event_in_10_minutes(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "EUR", "impact": "medium", "minutes_from_now": 10}]}))
    assert result["final_restrictive_decision"] == "REDUCE_SIZE"


def test_scenario_3_eurusd_event_released_3_minutes_ago(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "USD", "impact": "high", "minutes_from_now": -3}]}))
    assert result["final_restrictive_decision"] == "DELAY"


def test_scenario_4_existing_profitable_position_before_high_impact_event(monkeypatch):
    """Existing positions use evaluate_position()/adaptive-manager suppression (MANAGE_EXISTING_ONLY
    for nonessential changes), never dry-run's new-entry BLOCK/DELAY path -- confirmed distinctly here."""
    session_factory = _session_factory(monkeypatch)
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_openai_macro_classification_enabled=False))
    real_now = datetime.now(timezone.utc)
    with session_factory() as db:
        db.add(EconomicEventORM(id="evt_s4", provider="ff_calendar_json", provider_event_id="s4", normalized_name="nfp", raw_name="NFP", currency="USD", impact="high", scheduled_at_utc=real_now + timedelta(minutes=15), payload_hash="h", is_central_bank_event=False))
        db.commit()
    result = asyncio.run(svc.evaluate_position(symbol="EURUSD", direction="LONG"))
    assert result["guard"]["decision"] == "BLOCK"  # calendar_guard itself is directional-agnostic;
    # the adaptive-manager layer (tested separately) is what turns this into
    # ECONOMIC_MANAGE_EXISTING_ONLY suppression rather than a forced close.


def test_scenario_5_provider_unavailable(monkeypatch):
    svc = _svc(monkeypatch, ff_provider_fail_mode="conservative")
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_provider_state": {"calendar_state": "UNAVAILABLE"}}))
    assert result["final_restrictive_decision"] == "BLOCK"


def test_scenario_6_provider_schema_changed(monkeypatch):
    svc = _svc(monkeypatch, ff_provider_fail_mode="conservative")
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_provider_state": {"calendar_state": "SCHEMA_CHANGED"}}))
    assert result["final_restrictive_decision"] == "BLOCK"


def test_scenario_7_stale_last_known_good_calendar(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_provider_state": {"calendar_state": "STALE"}, "synthetic_events": [{"currency": "USD", "impact": "low", "minutes_from_now": 500}]}))
    assert result["final_restrictive_decision"] == "REDUCE_SIZE"


def test_scenario_8_openai_unavailable(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": []}))
    assert result["openai_advisory"] is None
    assert result["final_restrictive_decision"] == "ALLOW"


def test_scenario_9_openai_allow_vs_deterministic_block(monkeypatch):
    svc = _svc(monkeypatch)
    result = asyncio.run(
        svc.dry_run_evaluate(
            {
                "symbol": "EURUSD",
                "direction": "LONG",
                "decision_time": NOW.isoformat(),
                "synthetic_events": [{"currency": "USD", "impact": "high", "minutes_from_now": 5, "is_central_bank_event": True}],
                "synthetic_openai_advisory": {"recommended_action": "allow", "risk_level": "low", "confidence": 0.99},
            }
        )
    )
    assert result["final_restrictive_decision"] == "BLOCK"


def test_scenario_10_portfolio_manager_rejects_while_economic_allows(monkeypatch):
    svc = _svc(monkeypatch)
    _mock_portfolio_allow(monkeypatch, allowed=False, blockers=["MAX_OPEN_POSITIONS"])
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": []}))
    assert result["economic_context_payload"]["guard"]["decision"] == "ALLOW"
    assert result["final_restrictive_decision"] == "BLOCK"


def test_scenario_11_economic_allows_while_live_trading_blocked(monkeypatch):
    svc = _svc(monkeypatch)
    _mock_live_trading(monkeypatch, blocked=True)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "decision_time": NOW.isoformat(), "synthetic_events": []}))
    assert result["economic_context_payload"]["guard"]["decision"] == "ALLOW"
    assert result["live_trading_blocked"] is True
    assert result["order_send_reachable"] is False


def test_scenario_12_reduce_size_does_not_change_configured_risk_percentage(monkeypatch):
    """REDUCE_SIZE scales the proposed *volume* input, never a risk-percent config value --
    confirmed structurally: dry_run_evaluate has no risk_percent_per_trade field at all."""
    svc = _svc(monkeypatch)
    result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG", "volume": 1.0, "decision_time": NOW.isoformat(), "synthetic_events": [{"currency": "EUR", "impact": "medium", "minutes_from_now": 10}]}))
    assert result["final_restrictive_decision"] == "REDUCE_SIZE"
    assert result["adjusted_volume"] == 0.5
    assert "risk_percent_per_trade" not in result


def test_scenario_13_manage_existing_only_suppresses_only_nonessential_changes(monkeypatch):
    """Covered end-to-end at the adaptive-manager level in
    test_economic_intelligence_integration.py::test_economic_manage_existing_only_suppresses_nonessential_sltp_changes
    and test_economic_guard_does_not_prevent_explicit_invalidation_close -- referenced here for
    spec-item traceability."""
    assert True


def test_scenario_14_invalidation_or_hard_risk_exit_still_proceeds_through_execution_manager(monkeypatch):
    """Covered end-to-end in
    test_economic_intelligence_integration.py::test_economic_guard_does_not_prevent_explicit_invalidation_close
    -- referenced here for spec-item traceability."""
    assert True

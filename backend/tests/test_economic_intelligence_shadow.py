from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.economic_intelligence import calendar_guard, persistence
from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.orm import EconomicEventORM, EconomicTradeContextSnapshotORM
from backend.economic_intelligence.service import EconomicIntelligenceService
from backend.shared.db import Base

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(persistence, "SessionLocal", session_factory)
    return session_factory


def _block_result():
    return calendar_guard.empty_result("BLOCK", ["HIGH_IMPACT_EVENT_PRE_BLOCK"])


# --- pure apply_guard_mode tests (16, 19) ------------------------------------------------


def test_shadow_mode_never_blocks_execution_solely_due_to_economic_layer():
    effective, execution_changed = calendar_guard.apply_guard_mode(_block_result(), "shadow")
    assert effective["decision"] == "ALLOW"
    assert execution_changed is False


def test_disabled_mode_does_not_affect_final_decision():
    effective, execution_changed = calendar_guard.apply_guard_mode(_block_result(), "disabled")
    assert effective["decision"] == "ALLOW"
    assert execution_changed is False


def test_enforce_mode_block_still_blocks():
    effective, execution_changed = calendar_guard.apply_guard_mode(_block_result(), "enforce")
    assert effective["decision"] == "BLOCK"
    assert execution_changed is True


def test_unknown_mode_falls_back_to_enforce():
    effective, _changed = calendar_guard.apply_guard_mode(_block_result(), "not_a_real_mode")
    assert effective["decision"] == "BLOCK"


# --- full _evaluate() persistence across modes (17, 18) ----------------------------------


def test_shadow_decision_is_persisted_distinctly_from_effective_decision(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_economic_guard_mode="shadow", ff_openai_macro_classification_enabled=False))
    with session_factory() as db:
        db.add(
            EconomicEventORM(
                id="evt1", provider="ff_calendar_json", provider_event_id="1", normalized_name="nfp", raw_name="NFP", currency="USD", impact="high",
                scheduled_at_utc=datetime.now(timezone.utc) + timedelta(minutes=10), payload_hash="h", is_central_bank_event=False,
            )
        )
        db.commit()
    result = asyncio.run(svc.context_for_symbol("EURUSD"))
    assert result["guard"]["decision"] == "ALLOW"  # effective: shadow mode never restricts
    assert result["shadow_guard"]["decision"] == "BLOCK"  # true evaluated decision
    with session_factory() as db:
        row = db.query(EconomicTradeContextSnapshotORM).order_by(EconomicTradeContextSnapshotORM.created_at.desc()).first()
    assert row.economic_guard_mode == "shadow"
    assert row.shadow_decision == "BLOCK"
    assert row.effective_decision == "ALLOW"
    assert row.execution_changed_by_economic is False


def test_enforce_mode_persists_matching_shadow_and_effective_decision(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_economic_guard_mode="enforce", ff_openai_macro_classification_enabled=False))
    with session_factory() as db:
        db.add(
            EconomicEventORM(
                id="evt2", provider="ff_calendar_json", provider_event_id="2", normalized_name="nfp", raw_name="NFP", currency="USD", impact="high",
                scheduled_at_utc=datetime.now(timezone.utc) + timedelta(minutes=10), payload_hash="h", is_central_bank_event=False,
            )
        )
        db.commit()
    result = asyncio.run(svc.context_for_symbol("EURUSD"))
    assert result["guard"]["decision"] == "BLOCK"
    assert result["shadow_guard"]["decision"] == "BLOCK"
    with session_factory() as db:
        row = db.query(EconomicTradeContextSnapshotORM).order_by(EconomicTradeContextSnapshotORM.created_at.desc()).first()
    assert row.execution_changed_by_economic is True


# --- Portfolio Manager / live-trading precedence across every mode (20, 21) --------------


def test_portfolio_manager_block_wins_in_every_mode(monkeypatch):
    from backend.economic_intelligence import service as service_module

    for mode in ("disabled", "shadow", "enforce"):
        svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_economic_guard_mode=mode))
        monkeypatch.setattr(service_module.portfolio_manager, "can_open_new_trade", lambda: (False, ["MAX_OPEN_POSITIONS"]))
        monkeypatch.setattr(service_module, "query_events", lambda **kwargs: [])
        monkeypatch.setattr(service_module.provider_health, "health_snapshot", lambda config, next_due=None: {"items": [{"provider": "ff_calendar_json", "state": "HEALTHY", "freshness_seconds": 1}]})
        monkeypatch.setattr(service_module, "query_news", lambda **kwargs: [])
        monkeypatch.setattr(service_module, "save_trade_context_snapshot", lambda snapshot: "SNAP")
        result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG"}))
        assert result["final_restrictive_decision"] == "BLOCK", f"mode={mode}"
        assert result["portfolio_manager_decision"]["allowed"] is False


def test_live_trading_block_wins_in_every_mode(monkeypatch):
    from backend.economic_intelligence import service as service_module

    class _FakeCfg:
        live_trading_enabled = True

    for mode in ("disabled", "shadow", "enforce"):
        svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_economic_guard_mode=mode))
        monkeypatch.setattr(service_module.portfolio_manager, "can_open_new_trade", lambda: (True, []))
        monkeypatch.setattr(service_module, "query_events", lambda **kwargs: [])
        monkeypatch.setattr(service_module.provider_health, "health_snapshot", lambda config, next_due=None: {"items": [{"provider": "ff_calendar_json", "state": "HEALTHY", "freshness_seconds": 1}]})
        monkeypatch.setattr(service_module, "query_news", lambda **kwargs: [])
        monkeypatch.setattr(service_module, "save_trade_context_snapshot", lambda snapshot: "SNAP")
        monkeypatch.setattr(service_module, "mt5_config", lambda: _FakeCfg())
        result = asyncio.run(svc.dry_run_evaluate({"symbol": "EURUSD", "direction": "LONG"}))
        assert result["live_trading_blocked"] is True, f"mode={mode}"
        assert result["order_send_reachable"] is False, f"mode={mode}"


# --- shadow summary aggregation (22) ------------------------------------------------------


def test_shadow_summary_aggregates_correctly(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    with session_factory() as db:
        for i, (decision, effective) in enumerate([("BLOCK", "ALLOW"), ("BLOCK", "ALLOW"), ("ALLOW", "ALLOW"), ("DELAY", "ALLOW")]):
            db.add(
                EconomicTradeContextSnapshotORM(
                    id=f"snap{i}", symbol="EURUSD", deterministic_decision=decision, reason_codes_json=[],
                    economic_guard_mode="shadow", shadow_decision=decision, effective_decision=effective,
                    execution_changed_by_economic=False, provider_freshness_json={}, created_at=NOW,
                )
            )
        db.commit()
    svc = EconomicIntelligenceService()
    summary = asyncio.run(svc.shadow_summary())
    assert summary["total_evaluated_signals"] == 4
    assert summary["would_block"] == 2
    assert summary["would_allow"] == 1
    assert summary["would_delay"] == 1
    assert summary["executed_despite_shadow_block"] == 3  # BLOCK,BLOCK,DELAY all had effective=ALLOW


# --- OpenAI cannot alter the recorded deterministic shadow result (23) -------------------


def test_openai_advisory_cannot_alter_persisted_shadow_decision(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_economic_guard_mode="enforce", ff_openai_macro_classification_enabled=False))
    with session_factory() as db:
        db.add(
            EconomicEventORM(
                id="evt3", provider="ff_calendar_json", provider_event_id="3", normalized_name="nfp", raw_name="NFP", currency="USD", impact="high",
                scheduled_at_utc=datetime.now(timezone.utc) + timedelta(minutes=10), payload_hash="h", is_central_bank_event=False,
            )
        )
        db.commit()
    from backend.economic_intelligence import macro_context

    # Even if an advisory recommending ALLOW were present, combine_with_advisory can only
    # tighten (never loosen) the deterministic result -- verified directly against the same
    # combine path _evaluate() uses.
    calendar_result = calendar_guard.empty_result("BLOCK", ["HIGH_IMPACT_EVENT_PRE_BLOCK"])
    news_result = calendar_guard.empty_result("ALLOW")
    allow_advisory = {"recommended_action": "allow", "risk_level": "low", "confidence": 0.99}
    combined = macro_context.combine_with_advisory(calendar_result, news_result, advisory=allow_advisory)
    assert combined["decision"] == "BLOCK"
    result = asyncio.run(svc.context_for_symbol("EURUSD"))
    assert result["shadow_guard"]["decision"] == "BLOCK"

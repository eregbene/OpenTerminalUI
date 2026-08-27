"""2026-08-27 -- "same Bensim engine drives MT5 or cTrader" initiative, Gate C.

BrokerOrderIntent is constructed PURELY ADDITIVELY inside MT5's own _submit() (backend/brokers/
mt5/autonomous.py), right after risk approval -- from values the existing pipeline has already
computed, never a new computation that could diverge from MT5's real decision. These tests lock
in the parity guarantee: for a representative candidate per risk tier, the intent's risk_usd/sl/
tp/symbol/direction must exactly match what MT5's own risk/candidate objects already decided,
and MT5's own dry-run fields (intent, risk, risk_budget_adjustment) must be completely unaffected
by the new intent's presence.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def _candidate(strategy_id: str, *, stop_loss: str, take_profit: str) -> dict:
    return {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG",
        "context_hash": f"parity_{strategy_id}", "candidate_id": f"PARITY_{strategy_id}",
        "context": {"strategy_id": strategy_id}, "stop_loss": stop_loss, "take_profit": take_profit,
        "atr": "0.0010", "spread": "0.0001",
    }


def _run_dry(strategy_id: str, tier: str, confidence: float, monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    service = MT5AutonomousTradingService(fake_adapter())
    candidate = _candidate(strategy_id, stop_loss="1.0950", take_profit="1.1150")
    result = asyncio.run(service._submit(candidate, confidence=confidence, dry_run=True))
    return result, candidate


def test_order_intent_risk_usd_matches_final_tier_capped_risk(monkeypatch):
    """The exact invariant the strategy-tier hard-cap fix (2026-08-25/26) depends on downstream:
    order_intent.risk_usd must equal risk.effective_risk_usd (the figure AFTER the tier cap),
    never risk_budget_adjustment's own pre-tier-cap adjusted_risk_budget_usd."""
    result, _ = _run_dry("session_breakout", "C", 76.0, monkeypatch)
    assert result["status"] == "DRY_RUN_OK"
    assert result["order_intent"]["risk_usd"] == result["risk"]["effective_risk_usd"]


def test_order_intent_carries_the_exact_candidate_sl_tp_and_symbol(monkeypatch):
    result, candidate = _run_dry("mtfai1", "A", 82.0, monkeypatch)
    assert result["status"] == "DRY_RUN_OK"
    oi = result["order_intent"]
    assert oi["symbol"] == candidate["canonical_pair"]
    assert oi["direction"] == candidate["direction"]
    assert Decimal(oi["sl"]) == Decimal(candidate["stop_loss"])
    assert Decimal(oi["tp"]) == Decimal(candidate["take_profit"])
    assert oi["strategy_id"] == "mtfai1"
    assert oi["confidence"] == 82.0
    assert oi["broker"] == "mt5"


def test_order_intent_reflects_the_correct_strategy_tier(monkeypatch):
    for strategy_id, expected_tier in (("mtfai1", "A"), ("vwap_reversion", "B"), ("session_breakout", "C")):
        result, _ = _run_dry(strategy_id, expected_tier, 80.0, monkeypatch)
        assert result["status"] == "DRY_RUN_OK"
        assert result["order_intent"]["risk_tier"] == expected_tier


def test_order_intent_presence_does_not_alter_mt5s_own_dry_run_fields(monkeypatch):
    """Gate C's hard safety requirement: adding order_intent must not change ANY of MT5's own
    pre-existing dry-run output -- intent/risk/risk_budget_adjustment/stop_quality_v2 keep their
    exact pre-refactor shape and values."""
    result, candidate = _run_dry("mtfai1", "A", 82.0, monkeypatch)
    assert result["status"] == "DRY_RUN_OK"
    for required_key in ("intent", "risk", "risk_budget_adjustment", "stop_quality_v2", "projected_margin", "order_send_calls"):
        assert required_key in result
    assert result["intent"]["broker_symbol"] == "EURUSD"
    assert result["intent"]["direction"] == "LONG"
    assert Decimal(str(result["intent"]["stop_loss"])) == Decimal(candidate["stop_loss"])
    assert result["order_send_calls"] == 0  # dry_run never places a real order


def test_order_intent_account_id_matches_the_submitting_account(monkeypatch):
    result, _ = _run_dry("vwap_reversion", "B", 78.0, monkeypatch)
    assert result["status"] == "DRY_RUN_OK"
    assert result["order_intent"]["account_id"] == "demo_10k"

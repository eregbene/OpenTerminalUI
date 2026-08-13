"""DEMO-only MTFAI1 entry-quality experiment -- persistence side.

capture_cycle_candidate_evaluations() (backend/brokers/mt5/candidate_evaluation.py) attaches
the confirmation gate's verdict (mtfai1_confirmed / mtfai1_confirming_strategy_ids) onto
MT5CandidateEvaluationORM, but ONLY for the one executed candidate this cycle when it is
MTFAI1. Every other row -- non-MTFAI1, non-winning, non-executed, or when the gate wasn't
applicable/enabled -- must be left with mtfai1_confirmed=None so the comparison report's
UNCLASSIFIED bucket stays honest.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite


def _confidence(overall: float = 80.0) -> dict:
    return {"overall_score": overall, "band": "strong", "rule_version": "deterministic_confidence_v1", "components": []}


def _candidate(*, candidate_id: str, strategy_id: str, symbol: str = "EURUSD") -> dict:
    return {
        "candidate_id": candidate_id,
        "canonical_pair": symbol,
        "broker_symbol": symbol,
        "direction": "LONG",
        "trade_confidence": _confidence(),
        "rejection_reasons": [],
        "entry": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1100,
        "context": {"strategy_id": strategy_id, "timestamp": datetime.now(timezone.utc).isoformat()},
    }


def _executed_result(cycle_id: str, candidate: dict, *, confirmation_gate: dict | None, order_ticket: int = 555111) -> dict:
    trade = {"submission": {"status": "ACCEPTED", "order_ticket": order_ticket}, "intent": {"entry_price": "1.1001"}}
    return {"cycle_id": cycle_id, "status": "ORDER_SUBMITTED", "candidates": [candidate], "winner": candidate, "trade": trade, "confirmation_gate": confirmation_gate, "openai_calls": 0, "order_send_calls": 1}


def test_confirmed_mtfai1_winner_gets_tagged(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="CG1:EURUSD:x", strategy_id="mtfai1")
    gate = {"enabled": True, "applicable": True, "confirmed": True, "confirming_strategy_ids": ["momentum"], "deferred": False, "defer_reason": None, "executed_strategy_id": "mtfai1"}

    capture_cycle_candidate_evaluations(_executed_result("CG1", candidate, confirmation_gate=gate))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="CG1:EURUSD:x").one()
        assert row.mtfai1_confirmed is True
        assert row.mtfai1_confirming_strategy_ids == ["momentum"]


def test_standalone_mtfai1_gets_tagged_false(monkeypatch: pytest.MonkeyPatch):
    """A standalone MTFAI1 candidate only ever becomes the EXECUTED winner if no non-MTFAI1
    alternative existed this cycle (the gate's own never-force-a-trade guarantee) -- still a
    real, reachable state worth persisting accurately."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="CG2:EURUSD:x", strategy_id="mtfai1")
    gate = {"enabled": True, "applicable": True, "confirmed": False, "confirming_strategy_ids": [], "deferred": True, "defer_reason": "MTFAI1_CONFIRMATION_REQUIRED", "executed_strategy_id": "mtfai1"}

    capture_cycle_candidate_evaluations(_executed_result("CG2", candidate, confirmation_gate=gate, order_ticket=555222))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="CG2:EURUSD:x").one()
        assert row.mtfai1_confirmed is False


def test_non_mtfai1_winner_never_tagged(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="CG3:EURUSD:x", strategy_id="breakout")
    gate = {"enabled": True, "applicable": False, "confirmed": None, "confirming_strategy_ids": [], "deferred": False, "defer_reason": None, "executed_strategy_id": "breakout"}

    capture_cycle_candidate_evaluations(_executed_result("CG3", candidate, confirmation_gate=gate, order_ticket=555333))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="CG3:EURUSD:x").one()
        assert row.mtfai1_confirmed is None
        assert row.mtfai1_confirming_strategy_ids == []


def test_missing_confirmation_gate_leaves_row_untagged(monkeypatch: pytest.MonkeyPatch):
    """LIVE mode (gate disabled) or a cycle recorded before this experiment existed -- no
    confirmation_gate key in the result at all. Must not raise, must not guess."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="CG4:EURUSD:x", strategy_id="mtfai1")

    capture_cycle_candidate_evaluations(_executed_result("CG4", candidate, confirmation_gate=None, order_ticket=555444))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="CG4:EURUSD:x").one()
        assert row.mtfai1_confirmed is None
        assert row.mtfai1_confirming_strategy_ids == []


def test_non_winning_mtfai1_candidate_never_tagged(monkeypatch: pytest.MonkeyPatch):
    """A losing (non-winner) MTFAI1 candidate in the same cycle as the executed winner must not
    pick up the winner's confirmation verdict."""
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    winner = _candidate(candidate_id="CG5:EURUSD:x", strategy_id="breakout")
    loser_mtfai1 = _candidate(candidate_id="CG5:GBPUSD:y", strategy_id="mtfai1", symbol="GBPUSD")
    gate = {"enabled": True, "applicable": False, "confirmed": None, "confirming_strategy_ids": [], "deferred": False, "defer_reason": None, "executed_strategy_id": "breakout"}
    trade = {"submission": {"status": "ACCEPTED", "order_ticket": 555555}, "intent": {"entry_price": "1.1001"}}
    result = {"cycle_id": "CG5", "status": "ORDER_SUBMITTED", "candidates": [winner, loser_mtfai1], "winner": winner, "trade": trade, "confirmation_gate": gate, "openai_calls": 0, "order_send_calls": 1}

    capture_cycle_candidate_evaluations(result)

    with SessionLocal() as db:
        loser_row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="CG5:GBPUSD:y").one()
        assert loser_row.mtfai1_confirmed is None

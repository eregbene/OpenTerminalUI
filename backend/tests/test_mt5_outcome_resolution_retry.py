"""2026-08-19: real bug found while investigating why realized_pnl/realized_r are NULL on every
recently-closed MT5 trade. Root cause: AdaptivePositionStateORM.closed_detected_at is stamped
with utcnow() (true UTC), but opened_at is parsed from MT5's raw position timestamp via
datetime.fromtimestamp(value, tz=utc) -- which is wrong, because MT5 timestamps are broker
SERVER time, not UTC (this broker runs ~3h ahead). 529 of 534 tracked positions in production
showed closed_detected_at earlier than opened_at as a result. _executed_outcome_from_management_
records() used to treat "closed_detected_at is set" as license to finalize outcome_status=CLOSED
immediately, even when no real deal/cost data had synced yet -- writing every P&L field NULL
permanently, since pending_executed_candidates() only ever re-fetches outcome_status=PENDING rows.

These tests cover the fix directly (outcome_resolver.py::_executed_outcome_from_management_
records returning None -- i.e. "not ready yet, leave PENDING" -- instead of finalizing with nulls)
using the same DB-row-construction pattern as test_confidence_calibration.py's
test_executed_trade_links_back_to_original_candidate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM, MT5TradeRecordORM
from backend.brokers.mt5.outcome_resolver import CandidateOutcomeResolver
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_confidence_calibration import _candidate, _cycle_result
from backend.tests.test_mt5_adapter import fake_adapter


def _seed_candidate_and_position(*, candidate_id: str, ticket: str, realized_pnl: float | None) -> None:
    candidate = _candidate(candidate_id=candidate_id, entry=1.1000, sl=1.0950, tp=1.1100)
    trade = {"submission": {"status": "ACCEPTED", "order_ticket": int(ticket)}, "intent": {"entry_price": "1.1001"}}
    capture_cycle_candidate_evaluations(_cycle_result(candidate_id.split(":")[0], [candidate], winner=candidate, status="ORDER_SUBMITTED", trade=trade))

    with SessionLocal() as db:
        # opened_at deliberately AFTER closed_detected_at, reproducing the real broker-time-vs-
        # UTC skew that triggers this path in production rather than a hand-picked edge case.
        opened_at = datetime.now(timezone.utc)
        closed_at = opened_at - timedelta(hours=3)
        position = AdaptivePositionStateORM(position_id=ticket, symbol="EURUSD", direction="LONG", broker_ticket=ticket)
        position.entry_price = 1.1001
        position.opened_at = opened_at
        position.closed_detected_at = closed_at
        position.max_achieved_r = 1.8
        position.min_achieved_r = -0.2
        position.original_risk_money = 50.0
        position.winner_classification = "clean_winner"
        db.add(position)
        trade_row = MT5TradeRecordORM(trade_id=f"T_{ticket}", cycle_id=candidate_id.split(":")[0], candidate_id=candidate_id, symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", lot_size=0.1)
        trade_row.order_ticket = ticket
        trade_row.realized_pnl = realized_pnl
        trade_row.exit_reason = "TAKE_PROFIT"
        trade_row.open_timestamp = opened_at
        trade_row.close_timestamp = closed_at
        db.add(trade_row)
        db.commit()


def test_no_cost_data_does_not_finalize_stays_pending(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _seed_candidate_and_position(candidate_id="R1:EURUSD:x", ticket="700001", realized_pnl=None)

    resolver = CandidateOutcomeResolver(adapter=fake_adapter())
    linked = resolver._link_executed_outcomes()

    assert linked == 0
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="R1:EURUSD:x").one()
        assert row.outcome_status == "PENDING"
        assert row.realized_pnl is None


def test_resolves_once_cost_data_becomes_available_on_a_later_poll(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _seed_candidate_and_position(candidate_id="R2:EURUSD:x", ticket="700002", realized_pnl=None)

    resolver = CandidateOutcomeResolver(adapter=fake_adapter())
    first_pass = resolver._link_executed_outcomes()
    assert first_pass == 0

    # Deal data syncs in later -- exactly what the reconciliation import does a few minutes after.
    with SessionLocal() as db:
        trade_row = db.query(MT5TradeRecordORM).filter_by(order_ticket="700002").one()
        trade_row.realized_pnl = 90.0
        db.commit()

    second_pass = resolver._link_executed_outcomes()
    assert second_pass == 1
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="R2:EURUSD:x").one()
        assert row.outcome_status == "CLOSED"
        assert row.realized_pnl == 90.0


def test_real_zero_pnl_breakeven_is_not_treated_as_missing_data(monkeypatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    _seed_candidate_and_position(candidate_id="R3:EURUSD:x", ticket="700003", realized_pnl=0.0)

    resolver = CandidateOutcomeResolver(adapter=fake_adapter())
    linked = resolver._link_executed_outcomes()

    assert linked == 1
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="R3:EURUSD:x").one()
        assert row.outcome_status == "CLOSED"
        assert row.realized_pnl == 0.0

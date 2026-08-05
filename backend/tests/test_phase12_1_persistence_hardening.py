from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.portfolio.models import (
  Phase12Alert,
  Phase12AlertRule,
  Phase12AttributionRecord,
  Phase12ExecutionQuality,
  Phase12Incident,
  Phase12IncidentTimelineEntry,
  Phase12JournalEntry,
  Phase12JournalNote,
  Phase12LedgerEntry,
  Phase12OperationalHealth,
  Phase12PerformanceSnapshot,
  Phase12Portfolio,
  Phase12PortfolioMembership,
  Phase12ReplayEvent,
  Phase12ReplaySession,
  Phase12Report,
  Phase12ReportSchedule,
  Phase12RiskSnapshot,
  Phase12Snapshot,
  Phase12StrategyAllocation,
  PortfolioStatus,
)
from backend.portfolio.services import Phase12PortfolioService
from backend.shared.db import Base


PHASE12_TABLES = [
  Phase12Portfolio.__table__,
  Phase12PortfolioMembership.__table__,
  Phase12StrategyAllocation.__table__,
  Phase12Snapshot.__table__,
  Phase12LedgerEntry.__table__,
  Phase12PerformanceSnapshot.__table__,
  Phase12AttributionRecord.__table__,
  Phase12RiskSnapshot.__table__,
  Phase12ExecutionQuality.__table__,
  Phase12Alert.__table__,
  Phase12AlertRule.__table__,
  Phase12Incident.__table__,
  Phase12IncidentTimelineEntry.__table__,
  Phase12Report.__table__,
  Phase12ReportSchedule.__table__,
  Phase12ReplayEvent.__table__,
  Phase12ReplaySession.__table__,
  Phase12JournalEntry.__table__,
  Phase12JournalNote.__table__,
  Phase12OperationalHealth.__table__,
]


def _session_factory():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine, tables=[table for table in PHASE12_TABLES if table is not None])
  return sessionmaker(bind=engine)


def test_phase12_restart_persistence_and_idempotency() -> None:
  Session = _session_factory()
  db = Session()
  service = Phase12PortfolioService(db)
  portfolio = service.create_portfolio("user_a", {"name": "Durable", "status": PortfolioStatus.ACTIVE.value, "account_ids": ["paper_a"]})
  membership = service.attach_strategy(portfolio.portfolio_id, "user_a", {"deployment_id": "dep_a", "candidate_id": "cand_a", "approved": True})
  allocation = service.create_allocation(
    portfolio.portfolio_id,
    "user_a",
    {
      "deployment_id": "dep_a",
      "allocation_type": "FIXED_CAPITAL",
      "allocation_amount": 1000,
      "portfolio_equity": 10000,
      "max_gross_exposure": 2000,
      "max_net_exposure": 2000,
      "max_daily_loss": 500,
      "max_drawdown": 1000,
    },
  )
  service.approve_allocation(portfolio.portfolio_id, allocation.allocation_id, "user_a", {"reason": "fixture"})
  service.activate_allocation(portfolio.portfolio_id, allocation.allocation_id, "user_a")
  first_ledger = service.ingest_ledger_event(
    portfolio.portfolio_id,
    "user_a",
    {"source_identifier": "fill_1", "entry_type": "trade_cash_flow", "amount": -1000, "quantity_delta": 10, "instrument_id": "AAPL", "price": 100},
  )
  duplicate_ledger = service.ingest_ledger_event(
    portfolio.portfolio_id,
    "user_a",
    {"source_identifier": "fill_1", "entry_type": "trade_cash_flow", "amount": -1000, "quantity_delta": 10, "instrument_id": "AAPL", "price": 100},
  )
  snapshot = service.create_snapshot(portfolio.portfolio_id, "user_a", "portfolio_equity", {"equity": 9000, "valuation_status": "COMPLETE"})
  schedule = service.create_report_schedule("user_a", {"portfolio_id": portfolio.portfolio_id, "report_type": "DAILY"})
  replay = service.create_replay_session(portfolio.portfolio_id, "user_a", {})
  journal = service.generate_journal_entry("user_a", {"portfolio_id": portfolio.portfolio_id, "source_trade_id": "trade_1", "instrument_id": "AAPL"})
  portfolio_id = portfolio.portfolio_id
  membership_id = membership.membership_id
  ledger_entry_id = first_ledger.ledger_entry_id
  duplicate_ledger_entry_id = duplicate_ledger.ledger_entry_id
  snapshot_id = snapshot.snapshot_id
  schedule_id = schedule.schedule_id
  replay_session_id = replay.session_id
  journal_entry_id = journal.journal_entry_id
  db.close()

  db2 = Session()
  service2 = Phase12PortfolioService(db2)
  reloaded = service2.require_portfolio(portfolio_id, "user_a")
  assert reloaded.name == "Durable"
  assert service2.repo.memberships(portfolio_id)[0].membership_id == membership_id
  assert service2.repo.allocations(portfolio_id)[0].status == "ACTIVE"
  assert ledger_entry_id == duplicate_ledger_entry_id
  assert len(service2.repo.ledger(portfolio_id)) == 1
  assert service2.repo.snapshots(portfolio_id)[0].snapshot_id == snapshot_id
  assert service2.repo.report_schedule(schedule_id, "user_a") is not None
  assert service2.repo.replay_session(replay_session_id, "user_a") is not None
  assert service2.repo.journal_entry(journal_entry_id, "user_a") is not None


def test_phase12_cross_tenant_portfolio_access_is_rejected() -> None:
  Session = _session_factory()
  db = Session()
  service = Phase12PortfolioService(db)
  portfolio = service.create_portfolio("user_a", {"name": "Private"})
  try:
    service.require_portfolio(portfolio.portfolio_id, "user_b")
  except KeyError:
    pass
  else:
    raise AssertionError("cross-tenant portfolio access was not rejected")


def test_phase12_replay_control_is_read_only() -> None:
  Session = _session_factory()
  db = Session()
  service = Phase12PortfolioService(db)
  portfolio = service.create_portfolio("user_a", {"name": "Replay", "status": PortfolioStatus.ACTIVE.value})
  session = service.create_replay_session(portfolio.portfolio_id, "user_a", {})
  updated = service.control_replay_session(session.session_id, "user_a", {"control": "NEXT"})
  assert updated.read_only is True
  assert updated.cursor_sequence == 1
  assert service.require_portfolio(portfolio.portfolio_id, "user_a").status == PortfolioStatus.ACTIVE.value


def test_phase12_journal_generation_is_idempotent_and_ai_read_only() -> None:
  Session = _session_factory()
  db = Session()
  service = Phase12PortfolioService(db)
  portfolio = service.create_portfolio("user_a", {"name": "Journal"})
  first = service.generate_journal_entry("user_a", {"portfolio_id": portfolio.portfolio_id, "source_trade_id": "trade_1", "instrument_id": "EURUSD"})
  second = service.generate_journal_entry("user_a", {"portfolio_id": portfolio.portfolio_id, "source_trade_id": "trade_1", "instrument_id": "EURUSD"})
  assert first.journal_entry_id == second.journal_entry_id
  assert first.ai_narrative_json["can_mutate_trade_records"] is False

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.portfolio.models import (
  Phase12Alert,
  Phase12AlertRule,
  Phase12AttributionRecord,
  Phase12Incident,
  Phase12IncidentTimelineEntry,
  Phase12JournalEntry,
  Phase12JournalNote,
  Phase12LedgerEntry,
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
)


class PortfolioRepository:
  def __init__(self, db: Session):
    self.db = db

  def get_portfolio(self, portfolio_id: str, owner_user_id: str | None = None) -> Phase12Portfolio | None:
    query = self.db.query(Phase12Portfolio).filter(Phase12Portfolio.portfolio_id == portfolio_id)
    if owner_user_id is not None:
      query = query.filter(Phase12Portfolio.owner_user_id == owner_user_id)
    return query.first()

  def list_portfolios(self, owner_user_id: str, workspace_id: str | None = None, limit: int = 100, offset: int = 0) -> list[Phase12Portfolio]:
    query = self.db.query(Phase12Portfolio).filter(Phase12Portfolio.owner_user_id == owner_user_id)
    if workspace_id:
      query = query.filter(Phase12Portfolio.workspace_id == workspace_id)
    return query.order_by(Phase12Portfolio.created_at.desc()).offset(offset).limit(min(limit, 500)).all()

  def ledger(self, portfolio_id: str) -> list[Phase12LedgerEntry]:
    return self.db.query(Phase12LedgerEntry).filter(Phase12LedgerEntry.portfolio_id == portfolio_id).order_by(Phase12LedgerEntry.occurred_at.asc()).all()

  def get_ledger_by_source(self, source_event: str, source_identifier: str, entry_type: str) -> Phase12LedgerEntry | None:
    return (
      self.db.query(Phase12LedgerEntry)
      .filter(
        Phase12LedgerEntry.source_event == source_event,
        Phase12LedgerEntry.source_identifier == source_identifier,
        Phase12LedgerEntry.entry_type == entry_type,
      )
      .first()
    )

  def snapshots(self, portfolio_id: str, category: str | None = None) -> list[Phase12Snapshot]:
    query = self.db.query(Phase12Snapshot).filter(Phase12Snapshot.portfolio_id == portfolio_id)
    if category:
      query = query.filter(Phase12Snapshot.category == category)
    return query.order_by(Phase12Snapshot.as_of.desc()).all()

  def allocations(self, portfolio_id: str) -> list[Phase12StrategyAllocation]:
    return self.db.query(Phase12StrategyAllocation).filter(Phase12StrategyAllocation.portfolio_id == portfolio_id).order_by(Phase12StrategyAllocation.created_at.desc()).all()

  def memberships(self, portfolio_id: str) -> list[Phase12PortfolioMembership]:
    return self.db.query(Phase12PortfolioMembership).filter(Phase12PortfolioMembership.portfolio_id == portfolio_id).order_by(Phase12PortfolioMembership.created_at.desc()).all()

  def performance(self, portfolio_id: str) -> list[Phase12PerformanceSnapshot]:
    return self.db.query(Phase12PerformanceSnapshot).filter(Phase12PerformanceSnapshot.portfolio_id == portfolio_id).order_by(Phase12PerformanceSnapshot.as_of.desc()).all()

  def attribution(self, portfolio_id: str) -> list[Phase12AttributionRecord]:
    return self.db.query(Phase12AttributionRecord).filter(Phase12AttributionRecord.portfolio_id == portfolio_id).order_by(Phase12AttributionRecord.as_of.desc()).all()

  def risk_snapshots(self, portfolio_id: str) -> list[Phase12RiskSnapshot]:
    return self.db.query(Phase12RiskSnapshot).filter(Phase12RiskSnapshot.portfolio_id == portfolio_id).order_by(Phase12RiskSnapshot.as_of.desc()).all()

  def alerts(self, portfolio_id: str | None = None) -> list[Phase12Alert]:
    query = self.db.query(Phase12Alert)
    if portfolio_id:
      query = query.filter(Phase12Alert.portfolio_id == portfolio_id)
    return query.order_by(Phase12Alert.created_at.desc()).all()

  def incidents(self, portfolio_id: str | None = None) -> list[Phase12Incident]:
    query = self.db.query(Phase12Incident)
    if portfolio_id:
      query = query.filter(Phase12Incident.portfolio_id == portfolio_id)
    return query.order_by(Phase12Incident.created_at.desc()).all()

  def reports(self, owner_user_id: str) -> list[Phase12Report]:
    return self.db.query(Phase12Report).filter(Phase12Report.owner_user_id == owner_user_id).order_by(Phase12Report.created_at.desc()).all()

  def report_schedules(self, owner_user_id: str) -> list[Phase12ReportSchedule]:
    return self.db.query(Phase12ReportSchedule).filter(Phase12ReportSchedule.owner_user_id == owner_user_id).order_by(Phase12ReportSchedule.created_at.desc()).all()

  def report_schedule(self, schedule_id: str, owner_user_id: str) -> Phase12ReportSchedule | None:
    return self.db.query(Phase12ReportSchedule).filter(Phase12ReportSchedule.schedule_id == schedule_id, Phase12ReportSchedule.owner_user_id == owner_user_id).first()

  def replay_session(self, session_id: str, owner_user_id: str | None = None) -> Phase12ReplaySession | None:
    query = self.db.query(Phase12ReplaySession).filter(Phase12ReplaySession.session_id == session_id)
    if owner_user_id:
      query = query.filter(Phase12ReplaySession.owner_user_id == owner_user_id)
    return query.first()

  def replay_events(self, session_id: str) -> list[Phase12ReplayEvent]:
    return self.db.query(Phase12ReplayEvent).filter(Phase12ReplayEvent.session_id == session_id).order_by(Phase12ReplayEvent.sequence.asc()).all()

  def journal_entries(self, owner_user_id: str, portfolio_id: str | None = None) -> list[Phase12JournalEntry]:
    query = self.db.query(Phase12JournalEntry).filter(Phase12JournalEntry.owner_user_id == owner_user_id)
    if portfolio_id:
      query = query.filter(Phase12JournalEntry.portfolio_id == portfolio_id)
    return query.order_by(Phase12JournalEntry.created_at.desc()).all()

  def journal_entry(self, journal_entry_id: str, owner_user_id: str) -> Phase12JournalEntry | None:
    return self.db.query(Phase12JournalEntry).filter(Phase12JournalEntry.journal_entry_id == journal_entry_id, Phase12JournalEntry.owner_user_id == owner_user_id).first()

  def journal_notes(self, journal_entry_id: str, owner_user_id: str) -> list[Phase12JournalNote]:
    return self.db.query(Phase12JournalNote).filter(Phase12JournalNote.journal_entry_id == journal_entry_id, Phase12JournalNote.owner_user_id == owner_user_id).order_by(Phase12JournalNote.version.desc()).all()

  def alert_rules(self, owner_user_id: str) -> list[Phase12AlertRule]:
    return self.db.query(Phase12AlertRule).filter(Phase12AlertRule.owner_user_id == owner_user_id).order_by(Phase12AlertRule.created_at.desc()).all()

  def incident_timeline(self, incident_id: str) -> list[Phase12IncidentTimelineEntry]:
    return self.db.query(Phase12IncidentTimelineEntry).filter(Phase12IncidentTimelineEntry.incident_id == incident_id).order_by(Phase12IncidentTimelineEntry.created_at.asc()).all()

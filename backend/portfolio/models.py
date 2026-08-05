from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


def utcnow() -> datetime:
  return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
  return f"{prefix}_{uuid4().hex[:12]}"


class PortfolioStatus(str, Enum):
  DRAFT = "DRAFT"
  ACTIVE = "ACTIVE"
  PAUSED = "PAUSED"
  READ_ONLY = "READ_ONLY"
  ARCHIVED = "ARCHIVED"
  BLOCKED = "BLOCKED"


class AllocationType(str, Enum):
  FIXED_CAPITAL = "FIXED_CAPITAL"
  PORTFOLIO_PERCENT = "PORTFOLIO_PERCENT"
  RISK_BUDGET = "RISK_BUDGET"
  MAX_NOTIONAL = "MAX_NOTIONAL"
  MAX_MARGIN = "MAX_MARGIN"
  OBSERVATION_ONLY = "OBSERVATION_ONLY"


class ValuationStatus(str, Enum):
  COMPLETE = "COMPLETE"
  PARTIAL = "PARTIAL"
  STALE = "STALE"
  MISSING_MARK = "MISSING_MARK"
  MISSING_FX_RATE = "MISSING_FX_RATE"
  UNRECONCILED = "UNRECONCILED"
  INVALID = "INVALID"


class Phase12Portfolio(Base):
  __tablename__ = "phase12_portfolios"

  portfolio_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pf"))
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  workspace_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
  name: Mapped[str] = mapped_column(String(160), index=True)
  description: Mapped[str] = mapped_column(Text, default="")
  base_currency: Mapped[str] = mapped_column(String(8), default="USD")
  account_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
  status: Mapped[str] = mapped_column(String(24), default=PortfolioStatus.DRAFT.value, index=True)
  risk_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
  allocation_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
  paper_only: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
  version: Mapped[int] = mapped_column(Integer, default=1)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12PortfolioMembership(Base):
  __tablename__ = "phase12_portfolio_memberships"
  __table_args__ = (
    UniqueConstraint("portfolio_id", "deployment_id", "version", name="uq_phase12_membership_version"),
  )

  membership_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pfm"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  deployment_id: Mapped[str] = mapped_column(String(64), index=True)
  strategy_implementation_id: Mapped[str] = mapped_column(String(64), index=True)
  candidate_id: Mapped[str] = mapped_column(String(64), index=True)
  version: Mapped[int] = mapped_column(Integer, default=1)
  instrument_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
  timeframe: Mapped[str] = mapped_column(String(32), default="1D")
  status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
  activation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  deactivation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  allocation: Mapped[dict] = mapped_column(JSON, default=dict)
  risk_contribution_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
  owner: Mapped[str] = mapped_column(String(64), default="system")
  evidence_lineage: Mapped[dict] = mapped_column(JSON, default=dict)
  approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12StrategyAllocation(Base):
  __tablename__ = "phase12_strategy_allocations"

  allocation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("alloc"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  deployment_id: Mapped[str] = mapped_column(String(64), index=True)
  allocation_type: Mapped[str] = mapped_column(String(32), index=True)
  allocation_amount: Mapped[float] = mapped_column(Float, default=0.0)
  effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
  max_gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
  max_net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
  max_position_size: Mapped[float] = mapped_column(Float, default=0.0)
  max_daily_loss: Mapped[float] = mapped_column(Float, default=0.0)
  max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
  approval_record: Mapped[dict] = mapped_column(JSON, default=dict)
  status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
  version: Mapped[int] = mapped_column(Integer, default=1, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12Snapshot(Base):
  __tablename__ = "phase12_snapshots"
  __table_args__ = (
    UniqueConstraint("portfolio_id", "category", "content_hash", name="uq_phase12_snapshot_hash"),
    Index("ix_phase12_snapshot_portfolio_category_asof", "portfolio_id", "category", "as_of"),
  )

  snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("snap"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  account_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  category: Mapped[str] = mapped_column(String(48), index=True)
  as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
  received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  source: Mapped[str] = mapped_column(String(64), default="internal")
  source_version: Mapped[str] = mapped_column(String(64), default="phase12.v1")
  valuation_status: Mapped[str] = mapped_column(String(32), default=ValuationStatus.PARTIAL.value, index=True)
  currency: Mapped[str] = mapped_column(String(8), default="USD")
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  supersedes_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12LedgerEntry(Base):
  __tablename__ = "phase12_ledger_entries"
  __table_args__ = (
    UniqueConstraint("source_event", "source_identifier", "entry_type", name="uq_phase12_ledger_source"),
  )

  ledger_entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("led"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  account_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  strategy_deployment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  instrument_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  entry_type: Mapped[str] = mapped_column(String(40), index=True)
  source_event: Mapped[str] = mapped_column(String(64), index=True)
  source_identifier: Mapped[str] = mapped_column(String(128), index=True)
  currency: Mapped[str] = mapped_column(String(8), default="USD")
  amount: Mapped[float] = mapped_column(Float, default=0.0)
  quantity_delta: Mapped[float] = mapped_column(Float, default=0.0)
  price: Mapped[float | None] = mapped_column(Float, nullable=True)
  occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  audit_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12PerformanceSnapshot(Base):
  __tablename__ = "phase12_performance_snapshots"

  snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("perf"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  window: Mapped[str] = mapped_column(String(32), index=True)
  frequency: Mapped[str] = mapped_column(String(32), default="event")
  currency: Mapped[str] = mapped_column(String(8), default="USD")
  metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
  data_completeness: Mapped[str] = mapped_column(String(32), default="PARTIAL", index=True)
  as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12AttributionRecord(Base):
  __tablename__ = "phase12_attribution_records"
  __table_args__ = (
    Index("ix_phase12_attribution_portfolio_dimension_asof", "portfolio_id", "dimension", "as_of"),
  )

  attribution_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("attr"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  dimension: Mapped[str] = mapped_column(String(32), index=True)
  bucket: Mapped[str] = mapped_column(String(128), index=True)
  pnl: Mapped[float] = mapped_column(Float, default=0.0)
  residual: Mapped[float] = mapped_column(Float, default=0.0)
  currency: Mapped[str] = mapped_column(String(8), default="USD")
  as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12RiskSnapshot(Base):
  __tablename__ = "phase12_risk_snapshots"

  risk_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("risk"))
  portfolio_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_portfolios.portfolio_id", ondelete="CASCADE"), index=True)
  status: Mapped[str] = mapped_column(String(24), default="OK", index=True)
  gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
  net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
  leverage: Mapped[float] = mapped_column(Float, default=0.0)
  margin_utilization: Mapped[float] = mapped_column(Float, default=0.0)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12ExecutionQuality(Base):
  __tablename__ = "phase12_execution_quality"

  execution_quality_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("exq"))
  portfolio_id: Mapped[str] = mapped_column(String(64), index=True)
  order_id: Mapped[str] = mapped_column(String(64), index=True)
  side: Mapped[str] = mapped_column(String(8), index=True)
  quantity: Mapped[float] = mapped_column(Float, default=0.0)
  decision_price: Mapped[float | None] = mapped_column(Float, nullable=True)
  fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
  benchmark_json: Mapped[dict] = mapped_column(JSON, default=dict)
  latency_json: Mapped[dict] = mapped_column(JSON, default=dict)
  slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
  commission: Mapped[float] = mapped_column(Float, default=0.0)
  anomaly_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12Alert(Base):
  __tablename__ = "phase12_alerts"

  alert_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("alert"))
  scope: Mapped[str] = mapped_column(String(64), index=True)
  portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  rule_id: Mapped[str] = mapped_column(String(64), index=True)
  severity: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
  status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
  fingerprint: Mapped[str] = mapped_column(String(128), index=True)
  message: Mapped[str] = mapped_column(Text, default="")
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12AlertRule(Base):
  __tablename__ = "phase12_alert_rules"

  rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  workspace_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
  scope: Mapped[str] = mapped_column(String(64), index=True)
  severity: Mapped[str] = mapped_column(String(16), default="WARN", index=True)
  enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
  conditions_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12Incident(Base):
  __tablename__ = "phase12_incidents"

  incident_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("inc"))
  portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  title: Mapped[str] = mapped_column(String(240), index=True)
  severity: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
  status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
  timeline_json: Mapped[list[dict]] = mapped_column(JSON, default=list)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12IncidentTimelineEntry(Base):
  __tablename__ = "phase12_incident_timeline_entries"

  timeline_entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("inctl"))
  incident_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_incidents.incident_id", ondelete="CASCADE"), index=True)
  actor_user_id: Mapped[str] = mapped_column(String(64), index=True)
  event_type: Mapped[str] = mapped_column(String(48), index=True)
  message: Mapped[str] = mapped_column(Text, default="")
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12Report(Base):
  __tablename__ = "phase12_reports"

  report_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rpt"))
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  report_type: Mapped[str] = mapped_column(String(48), index=True)
  period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  status: Mapped[str] = mapped_column(String(24), default="GENERATED", index=True)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12ReportSchedule(Base):
  __tablename__ = "phase12_report_schedules"

  schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rpts"))
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  workspace_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
  portfolio_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  report_type: Mapped[str] = mapped_column(String(48), index=True)
  frequency: Mapped[str] = mapped_column(String(16), default="DAILY", index=True)
  timezone: Mapped[str] = mapped_column(String(64), default="UTC")
  enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
  next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
  last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
  retry_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12ReplayEvent(Base):
  __tablename__ = "phase12_replay_events"

  event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rpe"))
  session_id: Mapped[str] = mapped_column(String(64), index=True)
  portfolio_id: Mapped[str] = mapped_column(String(64), index=True)
  sequence: Mapped[int] = mapped_column(Integer, index=True)
  event_type: Mapped[str] = mapped_column(String(64), index=True)
  occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12ReplaySession(Base):
  __tablename__ = "phase12_replay_sessions"

  session_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rps"))
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  workspace_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
  portfolio_id: Mapped[str] = mapped_column(String(64), index=True)
  status: Mapped[str] = mapped_column(String(24), default="PAUSED", index=True)
  cursor_sequence: Mapped[int] = mapped_column(Integer, default=0)
  speed: Mapped[float] = mapped_column(Float, default=1.0)
  filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
  read_only: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12JournalEntry(Base):
  __tablename__ = "phase12_journal_entries"
  __table_args__ = (
    UniqueConstraint("portfolio_id", "source_trade_id", name="uq_phase12_journal_trade"),
  )

  journal_entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("jrnl"))
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  workspace_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
  portfolio_id: Mapped[str] = mapped_column(String(64), index=True)
  strategy_deployment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  instrument_id: Mapped[str] = mapped_column(String(64), index=True)
  source_trade_id: Mapped[str] = mapped_column(String(128), index=True)
  status: Mapped[str] = mapped_column(String(24), default="GENERATED", index=True)
  tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
  facts_json: Mapped[dict] = mapped_column(JSON, default=dict)
  metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
  ai_narrative_json: Mapped[dict] = mapped_column(JSON, default=dict)
  evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Phase12JournalNote(Base):
  __tablename__ = "phase12_journal_notes"

  note_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("jrnn"))
  journal_entry_id: Mapped[str] = mapped_column(String(64), ForeignKey("phase12_journal_entries.journal_entry_id", ondelete="CASCADE"), index=True)
  owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
  version: Mapped[int] = mapped_column(Integer, default=1, index=True)
  note_text: Mapped[str] = mapped_column(Text, default="")
  tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
  content_hash: Mapped[str] = mapped_column(String(128), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Phase12OperationalHealth(Base):
  __tablename__ = "phase12_operational_health"

  health_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ophealth"))
  component: Mapped[str] = mapped_column(String(64), index=True)
  status: Mapped[str] = mapped_column(String(24), index=True)
  freshness_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
  details_json: Mapped[dict] = mapped_column(JSON, default=dict)
  observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

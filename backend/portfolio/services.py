from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.portfolio.accounting import FillInput, build_average_cost_positions, cash_from_fills, stable_hash
from backend.portfolio.allocations import AllocationRequest, validate_allocation
from backend.portfolio.attribution import attribute_pnl
from backend.portfolio.drawdown import EquityPoint
from backend.portfolio.errors import PortfolioError
from backend.portfolio.exposure import calculate_exposure
from backend.portfolio.models import (
  Phase12Alert,
  Phase12AttributionRecord,
  Phase12Incident,
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
from backend.portfolio.performance import calculate_performance
from backend.portfolio.repositories import PortfolioRepository
from backend.portfolio.valuation import Mark, value_positions


class Phase12PortfolioService:
  def __init__(self, db: Session):
    self.db = db
    self.repo = PortfolioRepository(db)

  def create_portfolio(self, owner_user_id: str, payload: dict[str, Any]) -> Phase12Portfolio:
    row = Phase12Portfolio(
      owner_user_id=owner_user_id,
      workspace_id=str(payload.get("workspace_id") or "default"),
      name=str(payload.get("name") or "Paper Portfolio"),
      description=str(payload.get("description") or ""),
      base_currency=str(payload.get("base_currency") or "USD").upper(),
      account_ids=list(payload.get("account_ids") or []),
      status=str(payload.get("status") or PortfolioStatus.DRAFT.value),
      risk_policy_id=payload.get("risk_policy_id"),
      allocation_policy_id=payload.get("allocation_policy_id"),
      paper_only=True,
    )
    if row.status not in {item.value for item in PortfolioStatus}:
      raise PortfolioError("invalid portfolio status")
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def require_portfolio(self, portfolio_id: str, owner_user_id: str) -> Phase12Portfolio:
    row = self.repo.get_portfolio(portfolio_id, owner_user_id)
    if not row:
      raise KeyError("portfolio not found")
    return row

  def attach_strategy(self, portfolio_id: str, owner_user_id: str, payload: dict[str, Any]) -> Phase12PortfolioMembership:
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    deployment_id = str(payload.get("deployment_id") or "")
    if not deployment_id:
      raise PortfolioError("deployment_id is required")
    for existing in self.repo.memberships(portfolio_id):
      if existing.deployment_id == deployment_id and existing.status == "ACTIVE":
        raise PortfolioError("duplicate active strategy membership rejected")
    approved = bool(payload.get("approved"))
    if portfolio.status == PortfolioStatus.ACTIVE.value and not approved:
      raise PortfolioError("unapproved research candidates cannot join an active portfolio")
    row = Phase12PortfolioMembership(
      portfolio_id=portfolio.portfolio_id,
      deployment_id=str(payload.get("deployment_id") or ""),
      strategy_implementation_id=str(payload.get("strategy_implementation_id") or payload.get("strategy_id") or ""),
      candidate_id=str(payload.get("candidate_id") or ""),
      version=int(payload.get("version") or 1),
      instrument_scope=list(payload.get("instrument_scope") or []),
      timeframe=str(payload.get("timeframe") or "1D"),
      status="ACTIVE" if approved else "PENDING",
      activation_date=datetime.now(timezone.utc) if approved else None,
      allocation=dict(payload.get("allocation") or {}),
      risk_contribution_limit=payload.get("risk_contribution_limit"),
      owner=owner_user_id,
      evidence_lineage=dict(payload.get("evidence_lineage") or {}),
      approved=approved,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def create_allocation(self, portfolio_id: str, owner_user_id: str, payload: dict[str, Any]) -> Phase12StrategyAllocation:
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    memberships = {row.deployment_id: row for row in self.repo.memberships(portfolio_id)}
    deployment_id = str(payload.get("deployment_id") or "")
    membership = memberships.get(deployment_id)
    validate_allocation(
      AllocationRequest(
        portfolio_status=portfolio.status,
        deployment_approved=bool(membership and membership.approved),
        account_paper=portfolio.paper_only,
        portfolio_equity=float(payload.get("portfolio_equity") or payload.get("allocation_amount") or 0.0),
        allocation_type=str(payload.get("allocation_type") or ""),
        allocation_amount=float(payload.get("allocation_amount") or 0.0),
        max_gross_exposure=float(payload.get("max_gross_exposure") or 0.0),
        max_net_exposure=float(payload.get("max_net_exposure") or 0.0),
        max_daily_loss=float(payload.get("max_daily_loss") or 0.0),
        max_drawdown=float(payload.get("max_drawdown") or 0.0),
        emergency_blocked=portfolio.status == PortfolioStatus.BLOCKED.value,
      )
    )
    previous = self.repo.allocations(portfolio_id)
    version = max([row.version for row in previous if row.deployment_id == deployment_id] or [0]) + 1
    row = Phase12StrategyAllocation(
      portfolio_id=portfolio_id,
      deployment_id=deployment_id,
      allocation_type=str(payload.get("allocation_type")),
      allocation_amount=float(payload.get("allocation_amount") or 0.0),
      max_gross_exposure=float(payload.get("max_gross_exposure") or 0.0),
      max_net_exposure=float(payload.get("max_net_exposure") or 0.0),
      max_position_size=float(payload.get("max_position_size") or 0.0),
      max_daily_loss=float(payload.get("max_daily_loss") or 0.0),
      max_drawdown=float(payload.get("max_drawdown") or 0.0),
      approval_record=dict(payload.get("approval_record") or {}),
      status="PENDING",
      version=version,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def approve_allocation(self, portfolio_id: str, allocation_id: str, owner_user_id: str, approval: dict[str, Any]) -> Phase12StrategyAllocation:
    self.require_portfolio(portfolio_id, owner_user_id)
    row = self.db.query(Phase12StrategyAllocation).filter(
      Phase12StrategyAllocation.portfolio_id == portfolio_id,
      Phase12StrategyAllocation.allocation_id == allocation_id,
    ).first()
    if not row:
      raise KeyError("allocation not found")
    if row.status not in {"PENDING", "APPROVED"}:
      raise PortfolioError("only pending allocations can be approved")
    row.status = "APPROVED"
    row.approval_record = {"approved_by": owner_user_id, "approved_at": datetime.now(timezone.utc).isoformat(), **approval}
    self.db.commit()
    self.db.refresh(row)
    return row

  def activate_allocation(self, portfolio_id: str, allocation_id: str, owner_user_id: str) -> Phase12StrategyAllocation:
    self.require_portfolio(portfolio_id, owner_user_id)
    row = self.db.query(Phase12StrategyAllocation).filter(
      Phase12StrategyAllocation.portfolio_id == portfolio_id,
      Phase12StrategyAllocation.allocation_id == allocation_id,
    ).first()
    if not row:
      raise KeyError("allocation not found")
    if row.status != "APPROVED":
      raise PortfolioError("allocation must be approved before activation")
    now = datetime.now(timezone.utc)
    for existing in self.repo.allocations(portfolio_id):
      if existing.allocation_id != row.allocation_id and existing.deployment_id == row.deployment_id and existing.status == "ACTIVE":
        existing.status = "SUPERSEDED"
        existing.expiry_date = now
    row.status = "ACTIVE"
    row.effective_date = now
    self.db.commit()
    self.db.refresh(row)
    return row

  def set_portfolio_status(self, portfolio_id: str, owner_user_id: str, status: str) -> Phase12Portfolio:
    row = self.require_portfolio(portfolio_id, owner_user_id)
    if status not in {PortfolioStatus.ACTIVE.value, PortfolioStatus.PAUSED.value}:
      raise PortfolioError("unsupported portfolio status transition")
    row.status = status
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    self.db.commit()
    self.db.refresh(row)
    return row

  def ingest_ledger_event(self, portfolio_id: str, owner_user_id: str, event: dict[str, Any]) -> Phase12LedgerEntry:
    self.require_portfolio(portfolio_id, owner_user_id)
    entry_type = str(event.get("entry_type") or "trade_cash_flow")
    source_event = str(event.get("source_event") or "paper_fill")
    source_identifier = str(event.get("source_identifier") or event.get("fill_id") or event.get("order_id") or "")
    if not source_identifier:
      raise PortfolioError("source identifier is required")
    existing = self.repo.get_ledger_by_source(source_event, source_identifier, entry_type)
    if existing:
      return existing
    payload = dict(event)
    content_hash = stable_hash({"portfolio_id": portfolio_id, "entry_type": entry_type, "source_event": source_event, "source_identifier": source_identifier, "payload": payload})
    row = Phase12LedgerEntry(
      portfolio_id=portfolio_id,
      account_id=event.get("account_id"),
      strategy_deployment_id=event.get("strategy_deployment_id"),
      instrument_id=event.get("instrument_id") or event.get("symbol"),
      entry_type=entry_type,
      source_event=source_event,
      source_identifier=source_identifier,
      currency=str(event.get("currency") or "USD").upper(),
      amount=float(event.get("amount") or 0.0),
      quantity_delta=float(event.get("quantity_delta") or event.get("quantity") or 0.0),
      price=float(event["price"]) if event.get("price") is not None else None,
      occurred_at=event.get("occurred_at") if isinstance(event.get("occurred_at"), datetime) else datetime.now(timezone.utc),
      content_hash=content_hash,
      audit_reference=event.get("audit_reference"),
      payload_json=payload,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def create_snapshot(self, portfolio_id: str, owner_user_id: str, category: str, payload: dict[str, Any]) -> Phase12Snapshot:
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    content_hash = stable_hash({"category": category, "payload": payload, "portfolio_id": portfolio_id})
    row = Phase12Snapshot(
      portfolio_id=portfolio_id,
      account_id=payload.get("account_id"),
      category=category,
      as_of=payload.get("as_of") if isinstance(payload.get("as_of"), datetime) else datetime.now(timezone.utc),
      source=str(payload.get("source") or "internal"),
      source_version=str(payload.get("source_version") or "phase12.v1"),
      valuation_status=str(payload.get("valuation_status") or "PARTIAL"),
      currency=portfolio.base_currency,
      content_hash=content_hash,
      supersedes_snapshot_id=payload.get("supersedes_snapshot_id"),
      payload_json=payload,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def dashboard(self, portfolio_id: str, owner_user_id: str) -> dict[str, Any]:
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    ledger = self.repo.ledger(portfolio_id)
    cash = sum(float(row.amount) for row in ledger if row.entry_type in {"deposit", "withdrawal", "trade_cash_flow", "commission", "fee"})
    latest_equity = self.repo.snapshots(portfolio_id, "portfolio_equity")[:1]
    equity = float((latest_equity[0].payload_json or {}).get("equity") or cash or 0.0) if latest_equity else cash
    latest_exposure = self.repo.snapshots(portfolio_id, "exposure")[:1]
    exposure_payload = latest_exposure[0].payload_json if latest_exposure else {}
    alerts = [row for row in self.repo.alerts(portfolio_id) if row.status == "OPEN"]
    return {
      "portfolio_id": portfolio.portfolio_id,
      "name": portfolio.name,
      "status": portfolio.status,
      "base_currency": portfolio.base_currency,
      "total_equity": round(equity, 8),
      "cash": round(cash, 8),
      "daily_pnl": float(exposure_payload.get("daily_pnl") or 0.0),
      "total_return_pct": float(exposure_payload.get("total_return_pct") or 0.0),
      "drawdown_pct": float(exposure_payload.get("drawdown_pct") or 0.0),
      "gross_exposure": float(exposure_payload.get("gross_exposure") or 0.0),
      "net_exposure": float(exposure_payload.get("net_exposure") or 0.0),
      "margin_utilization": float(exposure_payload.get("margin_utilization") or 0.0),
      "active_strategies": len([row for row in self.repo.memberships(portfolio_id) if row.status == "ACTIVE"]),
      "alerts": [{"alert_id": row.alert_id, "severity": row.severity, "message": row.message} for row in alerts[:10]],
      "valuation_status": latest_equity[0].valuation_status if latest_equity else "PARTIAL",
      "paper_only": True,
    }

  def calculate_accounting(self, starting_cash: float, fills: list[dict], marks: dict[str, dict], base_currency: str = "USD") -> dict[str, Any]:
    fill_inputs = [FillInput(**row) for row in fills]
    positions = build_average_cost_positions(fill_inputs)
    mark_inputs = {symbol: Mark(symbol=symbol, **value) for symbol, value in marks.items()}
    valuation = value_positions(positions, mark_inputs, {(base_currency, base_currency): 1.0}, base_currency)
    cash = cash_from_fills(starting_cash, fill_inputs)
    realized = sum(row.realized_pnl for row in positions.values())
    equity = cash + valuation["position_value"]
    return {
      "cash": cash,
      "positions": {symbol: row.__dict__ for symbol, row in positions.items()},
      "realized_pnl": round(realized, 8),
      "unrealized_pnl": valuation["unrealized_pnl"],
      "equity": round(equity, 8),
      "valuation": valuation,
    }

  def analytics(self, portfolio_id: str, owner_user_id: str) -> dict[str, Any]:
    self.require_portfolio(portfolio_id, owner_user_id)
    snaps = list(reversed(self.repo.snapshots(portfolio_id, "portfolio_equity")))
    points = [
      EquityPoint(t=row.as_of, equity=float((row.payload_json or {}).get("equity") or 0.0))
      for row in snaps
      if isinstance(row.payload_json, dict)
    ]
    metrics = calculate_performance(points)
    attribution = attribute_pnl([], float(points[-1].equity - points[0].equity) if len(points) >= 2 else 0.0)
    return {"performance": metrics, "attribution": attribution, "equity_curve": [{"t": p.t.isoformat(), "equity": p.equity} for p in points]}

  def risk(self, portfolio_id: str, owner_user_id: str) -> dict[str, Any]:
    self.require_portfolio(portfolio_id, owner_user_id)
    latest_positions = self.repo.snapshots(portfolio_id, "position")[:1]
    positions = list((latest_positions[0].payload_json or {}).get("positions") or []) if latest_positions else []
    exposure = calculate_exposure(positions)
    equity = float((self.repo.snapshots(portfolio_id, "portfolio_equity")[:1] or [None])[0].payload_json.get("equity", 0.0)) if self.repo.snapshots(portfolio_id, "portfolio_equity") else 0.0
    leverage = exposure["gross_exposure"] / equity if equity else 0.0
    payload = {
      **exposure,
      "leverage": round(leverage, 8),
      "var": {"method": "historical", "status": "INSUFFICIENT_SAMPLE", "limitations": ["requires return history"]},
      "cvar": {"method": "historical", "status": "INSUFFICIENT_SAMPLE", "limitations": ["requires return history"]},
      "limit_status": "OK" if leverage <= 1.0 else "BREACH",
    }
    return payload

  def create_risk_snapshot(self, portfolio_id: str, owner_user_id: str) -> Phase12RiskSnapshot:
    payload = self.risk(portfolio_id, owner_user_id)
    row = Phase12RiskSnapshot(
      portfolio_id=portfolio_id,
      status=payload["limit_status"],
      gross_exposure=float(payload.get("gross_exposure") or 0.0),
      net_exposure=float(payload.get("net_exposure") or 0.0),
      leverage=float(payload.get("leverage") or 0.0),
      margin_utilization=float(payload.get("margin_utilization") or 0.0),
      payload_json=payload,
      content_hash=stable_hash(payload),
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def generate_report(self, owner_user_id: str, payload: dict[str, Any]) -> Phase12Report:
    report_payload = {"inputs": payload, "paper_only": True, "generated_at": datetime.now(timezone.utc).isoformat()}
    row = Phase12Report(
      owner_user_id=owner_user_id,
      portfolio_id=payload.get("portfolio_id"),
      report_type=str(payload.get("report_type") or "DAILY"),
      content_hash=stable_hash(report_payload),
      payload_json=report_payload,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def create_report_schedule(self, owner_user_id: str, payload: dict[str, Any]) -> Phase12ReportSchedule:
    portfolio_id = payload.get("portfolio_id")
    workspace_id = "default"
    if portfolio_id:
      portfolio = self.require_portfolio(str(portfolio_id), owner_user_id)
      workspace_id = portfolio.workspace_id
    row = Phase12ReportSchedule(
      owner_user_id=owner_user_id,
      workspace_id=workspace_id,
      portfolio_id=portfolio_id,
      report_type=str(payload.get("report_type") or "DAILY"),
      frequency=str(payload.get("frequency") or "DAILY").upper(),
      timezone=str(payload.get("timezone") or "UTC"),
      enabled=bool(payload.get("enabled", True)),
      next_run_at=payload.get("next_run_at") if isinstance(payload.get("next_run_at"), datetime) else None,
      retry_policy_json=dict(payload.get("retry_policy") or {"max_attempts": 3}),
      payload_json=dict(payload),
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def update_report_schedule(self, schedule_id: str, owner_user_id: str, payload: dict[str, Any]) -> Phase12ReportSchedule:
    row = self.repo.report_schedule(schedule_id, owner_user_id)
    if not row:
      raise KeyError("report schedule not found")
    if "enabled" in payload:
      row.enabled = bool(payload["enabled"])
    if "frequency" in payload:
      row.frequency = str(payload["frequency"]).upper()
    if "timezone" in payload:
      row.timezone = str(payload["timezone"])
    row.payload_json = {**dict(row.payload_json or {}), **dict(payload)}
    row.updated_at = datetime.now(timezone.utc)
    self.db.commit()
    self.db.refresh(row)
    return row

  def delete_report_schedule(self, schedule_id: str, owner_user_id: str) -> Phase12ReportSchedule:
    row = self.repo.report_schedule(schedule_id, owner_user_id)
    if not row:
      raise KeyError("report schedule not found")
    row.enabled = False
    row.updated_at = datetime.now(timezone.utc)
    self.db.commit()
    self.db.refresh(row)
    return row

  def create_replay_session(self, portfolio_id: str, owner_user_id: str, payload: dict[str, Any]) -> Phase12ReplaySession:
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    session = Phase12ReplaySession(
      owner_user_id=owner_user_id,
      workspace_id=portfolio.workspace_id,
      portfolio_id=portfolio_id,
      status="PAUSED",
      filters_json=dict(payload.get("filters") or {}),
      read_only=True,
    )
    self.db.add(session)
    self.db.flush()
    self.db.add(Phase12ReplayEvent(session_id=session.session_id, portfolio_id=portfolio_id, sequence=0, event_type="SESSION_CREATED", payload_json={"read_only": True}))
    self.db.commit()
    self.db.refresh(session)
    return session

  def control_replay_session(self, session_id: str, owner_user_id: str, payload: dict[str, Any]) -> Phase12ReplaySession:
    session = self.repo.replay_session(session_id, owner_user_id)
    if not session:
      raise KeyError("replay session not found")
    control = str(payload.get("control") or "PAUSE").upper()
    if control in {"PLAY", "RESUME"}:
      session.status = "PLAYING"
    elif control == "STOP":
      session.status = "STOPPED"
    elif control == "NEXT":
      session.cursor_sequence += 1
    elif control == "PREVIOUS":
      session.cursor_sequence = max(0, session.cursor_sequence - 1)
    else:
      session.status = "PAUSED"
    if payload.get("speed") is not None:
      session.speed = max(0.1, min(float(payload["speed"]), 20.0))
    self.db.commit()
    self.db.refresh(session)
    return session

  def generate_journal_entry(self, owner_user_id: str, payload: dict[str, Any]) -> Phase12JournalEntry:
    portfolio_id = str(payload.get("portfolio_id") or "")
    portfolio = self.require_portfolio(portfolio_id, owner_user_id)
    source_trade_id = str(payload.get("source_trade_id") or payload.get("order_id") or "")
    if not source_trade_id:
      raise PortfolioError("source_trade_id is required")
    existing = (
      self.db.query(Phase12JournalEntry)
      .filter(Phase12JournalEntry.portfolio_id == portfolio_id, Phase12JournalEntry.source_trade_id == source_trade_id)
      .first()
    )
    if existing:
      return existing
    facts = dict(payload.get("facts") or payload)
    metrics = dict(payload.get("metrics") or {})
    evidence = dict(payload.get("evidence") or {})
    ai_narrative = {
      "verified_facts": facts,
      "deterministic_metrics": metrics,
      "ai_interpretation": payload.get("ai_interpretation"),
      "missing_evidence": payload.get("missing_evidence", []),
      "can_mutate_trade_records": False,
    }
    content_hash = stable_hash({"portfolio_id": portfolio_id, "source_trade_id": source_trade_id, "facts": facts, "metrics": metrics, "evidence": evidence})
    row = Phase12JournalEntry(
      owner_user_id=owner_user_id,
      workspace_id=portfolio.workspace_id,
      portfolio_id=portfolio_id,
      strategy_deployment_id=payload.get("strategy_deployment_id"),
      instrument_id=str(payload.get("instrument_id") or payload.get("symbol") or "UNKNOWN"),
      source_trade_id=source_trade_id,
      facts_json=facts,
      metrics_json=metrics,
      ai_narrative_json=ai_narrative,
      evidence_json=evidence,
      content_hash=content_hash,
    )
    self.db.add(row)
    self.db.commit()
    self.db.refresh(row)
    return row

  def operational_health(self) -> dict[str, Any]:
    rows = self.db.query(Phase12OperationalHealth).order_by(Phase12OperationalHealth.observed_at.desc()).limit(50).all()
    if not rows:
      defaults = [
        ("database", "OK"),
        ("redis", "UNKNOWN"),
        ("backend", "OK"),
        ("broker_ibkr_paper", "SIMULATED"),
        ("portfolio_valuation", "PARTIAL"),
      ]
      for component, status in defaults:
        self.db.add(Phase12OperationalHealth(component=component, status=status, details_json={"phase": 12}))
      self.db.commit()
      rows = self.db.query(Phase12OperationalHealth).order_by(Phase12OperationalHealth.observed_at.desc()).limit(50).all()
    return {"components": [{"component": row.component, "status": row.status, "observed_at": row.observed_at.isoformat(), "details": row.details_json} for row in rows]}

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.auth.deps import get_current_user
from backend.execution_analytics import ExecutionAnalyticsService
from backend.models import User
from backend.operations import OperationsService
from backend.portfolio.errors import AllocationValidationError, PortfolioError
from backend.portfolio.models import Phase12Alert, Phase12Incident
from backend.portfolio.services import Phase12PortfolioService

router = APIRouter(prefix="/api", tags=["phase12"])


class PortfolioCreateRequest(BaseModel):
  name: str = "Paper Portfolio"
  description: str = ""
  base_currency: str = "USD"
  workspace_id: str = "default"
  account_ids: list[str] = Field(default_factory=list)
  status: str = "DRAFT"
  risk_policy_id: str | None = None
  allocation_policy_id: str | None = None


class StrategyMembershipRequest(BaseModel):
  deployment_id: str
  strategy_implementation_id: str = ""
  strategy_id: str = ""
  candidate_id: str
  version: int = 1
  instrument_scope: list[str] = Field(default_factory=list)
  timeframe: str = "1D"
  allocation: dict[str, Any] = Field(default_factory=dict)
  risk_contribution_limit: float | None = None
  evidence_lineage: dict[str, Any] = Field(default_factory=dict)
  approved: bool = False


class AllocationRequestBody(BaseModel):
  deployment_id: str
  allocation_type: str
  allocation_amount: float
  portfolio_equity: float
  max_gross_exposure: float
  max_net_exposure: float
  max_position_size: float = 0.0
  max_daily_loss: float
  max_drawdown: float
  approval_record: dict[str, Any] = Field(default_factory=dict)


class SnapshotRequest(BaseModel):
  category: str
  payload: dict[str, Any] = Field(default_factory=dict)


class AccountingRequest(BaseModel):
  starting_cash: float = 100000.0
  base_currency: str = "USD"
  fills: list[dict[str, Any]] = Field(default_factory=list)
  marks: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ExecutionAnalyticsRequest(BaseModel):
  order: dict[str, Any]
  fills: list[dict[str, Any]] = Field(default_factory=list)
  timestamps: dict[str, datetime | None] = Field(default_factory=dict)


class LedgerEventRequest(BaseModel):
  entry_type: str = "trade_cash_flow"
  source_event: str = "paper_fill"
  source_identifier: str
  account_id: str | None = None
  strategy_deployment_id: str | None = None
  instrument_id: str | None = None
  currency: str = "USD"
  amount: float = 0.0
  quantity_delta: float = 0.0
  price: float | None = None
  payload: dict[str, Any] = Field(default_factory=dict)


def _service(db: Session) -> Phase12PortfolioService:
  return Phase12PortfolioService(db)


def _portfolio_payload(row) -> dict[str, Any]:
  return {
    "portfolio_id": row.portfolio_id,
    "name": row.name,
    "description": row.description,
    "status": row.status,
    "base_currency": row.base_currency,
    "account_ids": row.account_ids,
    "paper_only": row.paper_only,
    "workspace_id": row.workspace_id,
    "version": row.version,
    "created_at": row.created_at.isoformat(),
  }


@router.get("/portfolios")
def list_portfolios(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return {"items": [_portfolio_payload(row) for row in _service(db).repo.list_portfolios(current_user.id)]}


@router.post("/portfolios")
def create_portfolio_plural(payload: PortfolioCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return create_portfolio(payload, db, current_user)


@router.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _portfolio_payload(_service(db).require_portfolio(portfolio_id, current_user.id))
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/portfolios/{portfolio_id}")
def patch_portfolio(portfolio_id: str, payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).require_portfolio(portfolio_id, current_user.id)
    for field in ["name", "description", "risk_policy_id", "allocation_policy_id"]:
      if field in payload:
        setattr(row, field, payload[field])
    row.version += 1
    db.commit()
    db.refresh(row)
    return _portfolio_payload(row)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/activate")
def activate_portfolio(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _portfolio_payload(_service(db).set_portfolio_status(portfolio_id, current_user.id, "ACTIVE"))
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/pause")
def pause_portfolio(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _portfolio_payload(_service(db).set_portfolio_status(portfolio_id, current_user.id, "PAUSED"))
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.post("/portfolio")
def create_portfolio(payload: PortfolioCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).create_portfolio(current_user.id, payload.model_dump())
    return {"portfolio_id": row.portfolio_id, "status": row.status, "paper_only": row.paper_only}
  except PortfolioError as exc:
    raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/portfolio/management")
def portfolio_management(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  repo = _service(db).repo
  return {
    "items": [
      {
        "portfolio_id": row.portfolio_id,
        "name": row.name,
        "status": row.status,
        "base_currency": row.base_currency,
        "account_ids": row.account_ids,
        "paper_only": row.paper_only,
        "version": row.version,
      }
      for row in repo.list_portfolios(current_user.id)
    ],
    "permissions": [
      "portfolio.view",
      "portfolio.create",
      "portfolio.update",
      "portfolio.manage_strategies",
      "portfolio.manage_allocations",
      "portfolio.pause",
      "portfolio.view_performance",
      "portfolio.view_risk",
    ],
  }


@router.get("/portfolio/dashboard")
def default_dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  portfolios = _service(db).repo.list_portfolios(current_user.id)
  if not portfolios:
    return {"status": "EMPTY", "paper_only": True, "valuation_status": "PARTIAL", "message": "No Phase 12 portfolio has been created."}
  return _service(db).dashboard(portfolios[0].portfolio_id, current_user.id)


@router.get("/portfolio/{portfolio_id}/dashboard")
def portfolio_dashboard(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _service(db).dashboard(portfolio_id, current_user.id)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/snapshot")
def latest_snapshot(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    _service(db).require_portfolio(portfolio_id, current_user.id)
    rows = _service(db).repo.snapshots(portfolio_id)
    return {"items": [{"snapshot_id": row.snapshot_id, "category": row.category, "valuation_status": row.valuation_status, "as_of": row.as_of.isoformat(), "payload": row.payload_json} for row in rows[:25]]}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/equity")
def portfolio_equity(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    analytics_payload = _service(db).analytics(portfolio_id, current_user.id)
    return {"items": analytics_payload["equity_curve"], "status": analytics_payload["performance"].get("status", "PARTIAL")}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/performance")
def portfolio_performance(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return performance(portfolio_id, db, current_user)


@router.get("/portfolios/{portfolio_id}/attribution")
def portfolio_attribution(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    payload = _service(db).analytics(portfolio_id, current_user.id)
    rows = _service(db).repo.attribution(portfolio_id)
    return {
      "calculated": payload["attribution"],
      "persisted": [{"dimension": row.dimension, "bucket": row.bucket, "pnl": row.pnl, "residual": row.residual, "as_of": row.as_of.isoformat()} for row in rows],
    }
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolio/{portfolio_id}/strategies")
def attach_strategy(portfolio_id: str, payload: StrategyMembershipRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).attach_strategy(portfolio_id, current_user.id, payload.model_dump())
    return {"membership_id": row.membership_id, "status": row.status, "approved": row.approved}
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/strategies")
def list_strategies(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    _service(db).require_portfolio(portfolio_id, current_user.id)
    return {"items": [{"membership_id": row.membership_id, "deployment_id": row.deployment_id, "status": row.status, "approved": row.approved, "version": row.version} for row in _service(db).repo.memberships(portfolio_id)]}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/strategies")
def attach_strategy_plural(portfolio_id: str, payload: StrategyMembershipRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return attach_strategy(portfolio_id, payload, db, current_user)


@router.delete("/portfolios/{portfolio_id}/strategies/{deployment_id}")
def deactivate_strategy(portfolio_id: str, deployment_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    _service(db).require_portfolio(portfolio_id, current_user.id)
    changed = 0
    for row in _service(db).repo.memberships(portfolio_id):
      if row.deployment_id == deployment_id and row.status == "ACTIVE":
        row.status = "INACTIVE"
        row.deactivation_date = datetime.now(timezone.utc)
        changed += 1
    db.commit()
    return {"deployment_id": deployment_id, "status": "INACTIVE", "changed": changed}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolio/{portfolio_id}/allocations")
def allocations(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    _service(db).require_portfolio(portfolio_id, current_user.id)
    return {
      "items": [
        {
          "allocation_id": row.allocation_id,
          "deployment_id": row.deployment_id,
          "allocation_type": row.allocation_type,
          "allocation_amount": row.allocation_amount,
          "status": row.status,
          "version": row.version,
          "created_at": row.created_at.isoformat(),
        }
        for row in _service(db).repo.allocations(portfolio_id)
      ]
    }
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolio/{portfolio_id}/allocations")
def create_allocation(portfolio_id: str, payload: AllocationRequestBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).create_allocation(portfolio_id, current_user.id, payload.model_dump())
    return {"allocation_id": row.allocation_id, "status": row.status, "version": row.version}
  except AllocationValidationError as exc:
    raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/allocations")
def allocations_plural(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return allocations(portfolio_id, db, current_user)


@router.post("/portfolios/{portfolio_id}/allocations")
def create_allocation_plural(portfolio_id: str, payload: AllocationRequestBody, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return create_allocation(portfolio_id, payload, db, current_user)


@router.post("/portfolios/{portfolio_id}/allocations/{allocation_id}/approve")
def approve_allocation(portfolio_id: str, allocation_id: str, payload: dict[str, Any] | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).approve_allocation(portfolio_id, allocation_id, current_user.id, payload or {})
    return {"allocation_id": row.allocation_id, "status": row.status, "version": row.version, "approval_record": row.approval_record}
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/allocations/{allocation_id}/activate")
def activate_allocation_route(portfolio_id: str, allocation_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).activate_allocation(portfolio_id, allocation_id, current_user.id)
    return {"allocation_id": row.allocation_id, "status": row.status, "version": row.version}
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/allocations/history")
def allocation_history(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return allocations(portfolio_id, db, current_user)


@router.post("/portfolios/{portfolio_id}/ledger/events")
def ingest_ledger_event(portfolio_id: str, payload: LedgerEventRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    data = {**payload.payload, **payload.model_dump(exclude={"payload"})}
    row = _service(db).ingest_ledger_event(portfolio_id, current_user.id, data)
    return {"ledger_entry_id": row.ledger_entry_id, "content_hash": row.content_hash, "source_identifier": row.source_identifier}
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.post("/portfolio/{portfolio_id}/snapshots")
def create_snapshot(portfolio_id: str, payload: SnapshotRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).create_snapshot(portfolio_id, current_user.id, payload.category, payload.payload)
    return {"snapshot_id": row.snapshot_id, "category": row.category, "content_hash": row.content_hash, "valuation_status": row.valuation_status}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolio/accounting/evaluate")
def accounting(payload: AccountingRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return _service(db).calculate_accounting(payload.starting_cash, payload.fills, payload.marks, payload.base_currency)


@router.get("/performance/{portfolio_id}")
def performance(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _service(db).analytics(portfolio_id, current_user.id)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/risk/{portfolio_id}/portfolio")
def portfolio_risk(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    return _service(db).risk(portfolio_id, current_user.id)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/risk")
def portfolio_risk_plural(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return portfolio_risk(portfolio_id, db, current_user)


@router.get("/portfolios/{portfolio_id}/risk/history")
def portfolio_risk_history(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    _service(db).require_portfolio(portfolio_id, current_user.id)
    rows = _service(db).repo.risk_snapshots(portfolio_id)
    return {"items": [{"risk_snapshot_id": row.risk_snapshot_id, "status": row.status, "leverage": row.leverage, "as_of": row.as_of.isoformat(), "payload": row.payload_json} for row in rows]}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/risk/stress-test")
def portfolio_stress_test(portfolio_id: str, payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    base = _service(db).risk(portfolio_id, current_user.id)
    shock = float(payload.get("market_shock_pct") or 0.0)
    return {"read_only": True, "mutated_state": False, "shock": shock, "estimated_pnl_change": round(float(base.get("gross_exposure") or 0.0) * shock, 8), "base": base}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/execution-analytics/evaluate")
def execution_analytics(payload: ExecutionAnalyticsRequest) -> dict[str, Any]:
  return ExecutionAnalyticsService().analyze(payload.order, payload.fills, payload.timestamps)


@router.get("/operations/health")
def operations_health(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return _service(db).operational_health()


@router.get("/operations/components")
def operations_components(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return _service(db).operational_health()


@router.get("/operations/timeline")
def operations_timeline(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  incidents = db.query(Phase12Incident).order_by(Phase12Incident.updated_at.desc()).limit(50).all()
  return {"items": [{"incident_id": row.incident_id, "title": row.title, "status": row.status, "timeline": row.timeline_json} for row in incidents]}


@router.get("/operations/incidents")
def incidents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = db.query(Phase12Incident).order_by(Phase12Incident.created_at.desc()).all()
  return {"items": [{"incident_id": row.incident_id, "title": row.title, "severity": row.severity, "status": row.status} for row in rows]}


@router.get("/operations/incidents/{incident_id}")
def incident(incident_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  row = db.query(Phase12Incident).filter(Phase12Incident.incident_id == incident_id).first()
  if not row:
    raise HTTPException(status_code=404, detail="incident not found")
  return {"incident_id": row.incident_id, "title": row.title, "severity": row.severity, "status": row.status, "timeline": row.timeline_json}


@router.post("/operations/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = OperationsService(db).acknowledge_incident(incident_id)
    return {"incident_id": row.incident_id, "status": row.status}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/operations/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = OperationsService(db).resolve_incident(incident_id)
    return {"incident_id": row.incident_id, "status": row.status}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/operations/alerts")
def alerts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = db.query(Phase12Alert).order_by(Phase12Alert.created_at.desc()).all()
  return {"items": [{"alert_id": row.alert_id, "severity": row.severity, "status": row.status, "message": row.message} for row in rows]}


@router.get("/operations/alerts/rules")
def alert_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = _service(db).repo.alert_rules(current_user.id)
  if rows:
    return {"items": [{"rule_id": row.rule_id, "severity": row.severity, "enabled": row.enabled, "scope": row.scope} for row in rows]}
  return {"items": [{"rule_id": "portfolio_stale_valuation", "severity": "WARN", "enabled": True}, {"rule_id": "risk_limit_breach", "severity": "CRITICAL", "enabled": True}]}


@router.post("/operations/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = OperationsService(db).acknowledge_alert(alert_id)
    return {"alert_id": row.alert_id, "status": row.status}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/reports")
def reports(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = _service(db).repo.reports(current_user.id)
  return {"items": [{"report_id": row.report_id, "report_type": row.report_type, "status": row.status, "created_at": row.created_at.isoformat()} for row in rows]}


@router.post("/reports/generate")
def generate_report(payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  row = _service(db).generate_report(current_user.id, payload)
  return {"report_id": row.report_id, "status": row.status, "content_hash": row.content_hash}


@router.get("/reports/schedules")
def report_schedules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = _service(db).repo.report_schedules(current_user.id)
  return {
    "items": [
      {
        "schedule_id": row.schedule_id,
        "report_type": row.report_type,
        "frequency": row.frequency,
        "enabled": row.enabled,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
      }
      for row in rows
    ]
  }


@router.post("/reports/schedules")
def create_report_schedule(payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  row = _service(db).create_report_schedule(current_user.id, payload)
  return {"schedule_id": row.schedule_id, "status": "CREATED", "enabled": row.enabled}


@router.patch("/reports/schedules/{schedule_id}")
def update_report_schedule(schedule_id: str, payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).update_report_schedule(schedule_id, current_user.id, payload)
    return {"schedule_id": row.schedule_id, "status": "UPDATED", "enabled": row.enabled}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/reports/schedules/{schedule_id}")
def delete_report_schedule(schedule_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).delete_report_schedule(schedule_id, current_user.id)
    return {"schedule_id": row.schedule_id, "status": "DISABLED", "enabled": row.enabled}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/reports/{report_id}")
def report(report_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = [row for row in _service(db).repo.reports(current_user.id) if row.report_id == report_id]
  if not rows:
    raise HTTPException(status_code=404, detail="report not found")
  row = rows[0]
  return {"report_id": row.report_id, "report_type": row.report_type, "status": row.status, "payload": row.payload_json}


@router.get("/reports/{report_id}/download")
def report_download(report_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  return report(report_id, db, current_user)


@router.post("/replay/sessions")
def create_replay_session(payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  portfolio_id = str(payload.get("portfolio_id") or "")
  try:
    session = _service(db).create_replay_session(portfolio_id, current_user.id, payload)
    return {"session_id": session.session_id, "portfolio_id": session.portfolio_id, "status": session.status, "read_only": session.read_only}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/replay/sessions/{session_id}")
def get_replay_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  session = _service(db).repo.replay_session(session_id, current_user.id)
  if not session:
    raise HTTPException(status_code=404, detail="replay session not found")
  events = _service(db).repo.replay_events(session_id)
  return {"session_id": session_id, "portfolio_id": session.portfolio_id, "event_count": len(events), "read_only": session.read_only, "status": session.status, "cursor_sequence": session.cursor_sequence, "speed": session.speed}


@router.get("/replay/sessions/{session_id}/events")
def get_replay_events(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  session = get_replay_session(session_id, db, current_user)
  return {"session": session, "items": [{"sequence": row.sequence, "event_type": row.event_type, "occurred_at": row.occurred_at.isoformat(), "payload": row.payload_json} for row in _service(db).repo.replay_events(session_id)]}


@router.post("/replay/sessions/{session_id}/control")
def control_replay_session(session_id: str, payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    session = _service(db).control_replay_session(session_id, current_user.id, payload)
    return {"session_id": session_id, "control": payload.get("control", "PAUSE"), "status": session.status, "cursor_sequence": session.cursor_sequence, "read_only": session.read_only, "mutated_trading_state": False}
  except KeyError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/replay/sessions/{session_id}")
def delete_replay_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  session = _service(db).repo.replay_session(session_id, current_user.id)
  if not session:
    raise HTTPException(status_code=404, detail="replay session not found")
  session.status = "DELETED"
  db.commit()
  return {"session_id": session_id, "status": "DELETED", "read_only": session.read_only}


@router.get("/journal")
def journal_entries(portfolio_id: str | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  rows = _service(db).repo.journal_entries(current_user.id, portfolio_id)
  return {"items": [{"journal_entry_id": row.journal_entry_id, "portfolio_id": row.portfolio_id, "instrument_id": row.instrument_id, "status": row.status, "created_at": row.created_at.isoformat()} for row in rows]}


@router.get("/journal/{entry_id}")
def journal_entry(entry_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  row = _service(db).repo.journal_entry(entry_id, current_user.id)
  if not row:
    raise HTTPException(status_code=404, detail="journal entry not found")
  return {"journal_entry_id": row.journal_entry_id, "facts": row.facts_json, "metrics": row.metrics_json, "ai_narrative": row.ai_narrative_json, "evidence": row.evidence_json}


@router.post("/journal")
def generate_journal_entry(payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  try:
    row = _service(db).generate_journal_entry(current_user.id, payload)
    return {"journal_entry_id": row.journal_entry_id, "content_hash": row.content_hash, "status": row.status}
  except (PortfolioError, KeyError) as exc:
    raise HTTPException(status_code=400 if isinstance(exc, PortfolioError) else 404, detail=str(exc)) from exc


@router.patch("/journal/{entry_id}")
def update_journal_entry(entry_id: str, payload: dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
  row = _service(db).repo.journal_entry(entry_id, current_user.id)
  if not row:
    raise HTTPException(status_code=404, detail="journal entry not found")
  row.tags_json = list(payload.get("tags") or row.tags_json or [])
  row.status = str(payload.get("status") or row.status)
  db.commit()
  return {"journal_entry_id": row.journal_entry_id, "status": row.status, "tags": row.tags_json}

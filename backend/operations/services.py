from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.portfolio.models import Phase12Alert, Phase12Incident


class OperationsService:
  def __init__(self, db: Session):
    self.db = db

  def acknowledge_alert(self, alert_id: str) -> Phase12Alert:
    row = self.db.query(Phase12Alert).filter(Phase12Alert.alert_id == alert_id).first()
    if not row:
      raise KeyError("alert not found")
    row.status = "ACKNOWLEDGED"
    row.acknowledged_at = datetime.now(timezone.utc)
    self.db.commit()
    self.db.refresh(row)
    return row

  def acknowledge_incident(self, incident_id: str) -> Phase12Incident:
    row = self.db.query(Phase12Incident).filter(Phase12Incident.incident_id == incident_id).first()
    if not row:
      raise KeyError("incident not found")
    row.status = "ACKNOWLEDGED"
    row.acknowledged_at = datetime.now(timezone.utc)
    row.timeline_json = [*list(row.timeline_json or []), {"event": "acknowledged", "at": row.acknowledged_at.isoformat()}]
    self.db.commit()
    self.db.refresh(row)
    return row

  def resolve_incident(self, incident_id: str) -> Phase12Incident:
    row = self.db.query(Phase12Incident).filter(Phase12Incident.incident_id == incident_id).first()
    if not row:
      raise KeyError("incident not found")
    row.status = "RESOLVED"
    row.resolved_at = datetime.now(timezone.utc)
    row.timeline_json = [*list(row.timeline_json or []), {"event": "resolved", "at": row.resolved_at.isoformat()}]
    self.db.commit()
    self.db.refresh(row)
    return row

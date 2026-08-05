from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExecutionLineage:
  signal_generated: datetime | None = None
  risk_evaluation_started: datetime | None = None
  risk_approved: datetime | None = None
  oms_order_created: datetime | None = None
  user_approved: datetime | None = None
  broker_submission_started: datetime | None = None
  broker_acknowledged: datetime | None = None
  first_fill: datetime | None = None
  final_fill: datetime | None = None
  commission_received: datetime | None = None
  reconciliation_completed: datetime | None = None

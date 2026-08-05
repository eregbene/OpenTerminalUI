from __future__ import annotations

from datetime import datetime


STAGES = [
  ("signal_to_risk_ms", "signal_generated", "risk_evaluation_started"),
  ("risk_duration_ms", "risk_evaluation_started", "risk_approved"),
  ("approval_to_oms_ms", "risk_approved", "oms_order_created"),
  ("user_approval_ms", "oms_order_created", "user_approved"),
  ("broker_ack_ms", "broker_submission_started", "broker_acknowledged"),
  ("first_fill_ms", "broker_acknowledged", "first_fill"),
  ("completion_ms", "first_fill", "final_fill"),
  ("reconciliation_ms", "final_fill", "reconciliation_completed"),
]


def latency_by_stage(timestamps: dict[str, datetime | None]) -> dict:
  out = {}
  for label, start_key, end_key in STAGES:
    start = timestamps.get(start_key)
    end = timestamps.get(end_key)
    out[label] = round((end - start).total_seconds() * 1000.0, 3) if start and end else None
  return out

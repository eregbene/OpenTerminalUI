def risk_snapshot_payload(metrics: dict) -> dict:
  return {"category": "risk", "metrics": metrics, "mutates_state": False}

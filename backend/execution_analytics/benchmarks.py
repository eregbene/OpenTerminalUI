from __future__ import annotations


def valid_benchmarks(order: dict) -> dict:
  out = {}
  for key in ["decision_price", "quote_at_order_creation", "midpoint_at_submission", "arrival_price", "limit_price", "vwap", "closing_price"]:
    value = order.get(key)
    timestamp = order.get(f"{key}_timestamp") or order.get("timestamp")
    out[key] = {"value": value, "status": "COMPLETE" if value is not None and timestamp is not None else "MISSING_INPUT"}
  return out

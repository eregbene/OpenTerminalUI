from __future__ import annotations

from backend.execution_analytics.benchmarks import valid_benchmarks
from backend.execution_analytics.fills import fill_quality
from backend.execution_analytics.latency import latency_by_stage
from backend.execution_analytics.slippage import calculate_slippage


class ExecutionAnalyticsService:
  def analyze(self, order: dict, fills: list[dict], timestamps: dict) -> dict:
    first_fill = fills[0] if fills else {}
    decision_price = float(order.get("decision_price") or order.get("limit_price") or first_fill.get("price") or 0.0)
    fill_price = float(first_fill.get("price") or decision_price)
    side = str(order.get("side") or "BUY")
    quantity = float(order.get("quantity") or first_fill.get("quantity") or 0.0)
    commission = sum(float(row.get("commission") or row.get("fees") or 0.0) for row in fills)
    slippage = calculate_slippage(side, decision_price, fill_price, quantity, commission)
    quality = fill_quality(quantity, fills)
    anomalies = []
    if abs(slippage["basis_point_slippage"]) > 50:
      anomalies.append("HIGH_SLIPPAGE")
    if quality["fill_ratio"] < 1:
      anomalies.append("PARTIAL_FILL")
    latency = latency_by_stage(timestamps)
    if latency.get("broker_ack_ms") and latency["broker_ack_ms"] > 5000:
      anomalies.append("DELAYED_ACKNOWLEDGEMENT")
    return {
      "latency": latency,
      "slippage": slippage,
      "benchmarks": valid_benchmarks(order),
      "fill_quality": quality,
      "anomalies": anomalies,
    }

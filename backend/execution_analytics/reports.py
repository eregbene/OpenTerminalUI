def execution_report(order_count: int, analyses: list[dict]) -> dict:
  rejection_rate = 0.0
  fill_rate = sum(1 for row in analyses if row.get("fill_quality", {}).get("fill_ratio", 0) >= 1) / order_count if order_count else 0.0
  avg_slippage = sum(float(row.get("slippage", {}).get("basis_point_slippage") or 0.0) for row in analyses) / len(analyses) if analyses else 0.0
  return {"order_count": order_count, "fill_rate": fill_rate, "rejection_rate": rejection_rate, "average_slippage_bps": avg_slippage}

from __future__ import annotations


def calculate_slippage(side: str, decision_price: float, fill_price: float, quantity: float, commission: float = 0.0) -> dict:
  side_upper = side.upper()
  direction = 1.0 if side_upper in {"BUY", "COVER"} else -1.0
  signed = (float(fill_price) - float(decision_price)) * direction
  bps = signed / float(decision_price) * 10000.0 if decision_price else 0.0
  gross_cost = signed * abs(float(quantity))
  return {
    "absolute_slippage": round(signed, 8),
    "basis_point_slippage": round(bps, 8),
    "signed_slippage": round(signed, 8),
    "gross_slippage_cost": round(gross_cost, 8),
    "commission": round(float(commission), 8),
    "total_execution_cost": round(gross_cost + float(commission), 8),
  }

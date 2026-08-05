from __future__ import annotations


def apply_percentage_shock(position_rows: list[dict], shock_pct: float) -> dict:
  pnl = sum(float(row.get("market_value") or 0.0) * float(shock_pct) for row in position_rows)
  return {"scenario": "percentage_shock", "shock_pct": shock_pct, "pnl": round(pnl, 8), "mutates_state": False}

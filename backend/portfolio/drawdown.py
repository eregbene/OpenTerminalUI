from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EquityPoint:
  t: datetime
  equity: float


def calculate_drawdown(points: list[EquityPoint]) -> dict:
  ordered = sorted(points, key=lambda row: row.t)
  if not ordered:
    return {"current_drawdown_pct": None, "max_drawdown_pct": None, "underwater": [], "sample_count": 0}
  peak = ordered[0].equity
  peak_time = ordered[0].t
  max_dd = 0.0
  max_start = peak_time
  trough_time = peak_time
  underwater = []
  recovery_time = None
  for point in ordered:
    if point.equity >= peak:
      peak = point.equity
      peak_time = point.t
      if max_dd < 0 and recovery_time is None:
        recovery_time = point.t
    dd = 0.0 if peak == 0 else (point.equity / peak - 1.0) * 100.0
    underwater.append({"t": point.t.isoformat(), "drawdown_pct": round(dd, 8)})
    if dd < max_dd:
      max_dd = dd
      max_start = peak_time
      trough_time = point.t
      recovery_time = None
  current_dd = underwater[-1]["drawdown_pct"]
  return {
    "current_drawdown_pct": current_dd,
    "max_drawdown_pct": round(max_dd, 8),
    "drawdown_start": max_start.isoformat(),
    "trough": trough_time.isoformat(),
    "recovery": recovery_time.isoformat() if recovery_time else None,
    "duration_observations": sum(1 for row in underwater if row["drawdown_pct"] < 0),
    "underwater": underwater,
    "sample_count": len(ordered),
  }

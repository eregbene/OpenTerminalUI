from __future__ import annotations


def historical_var_cvar(returns: list[float], confidence: float = 0.95, horizon_days: int = 1) -> dict:
  clean = sorted(float(row) for row in returns if row is not None)
  if len(clean) < 20:
    return {
      "status": "INSUFFICIENT_SAMPLE",
      "method": "historical",
      "confidence": confidence,
      "horizon_days": horizon_days,
      "sample_count": len(clean),
      "var": None,
      "cvar": None,
      "limitations": ["VaR is not a maximum possible loss", "requires at least 20 observations"],
    }
  idx = max(0, int((1.0 - confidence) * len(clean)) - 1)
  tail = clean[: idx + 1]
  return {
    "status": "COMPLETE",
    "method": "historical",
    "confidence": confidence,
    "horizon_days": horizon_days,
    "sample_count": len(clean),
    "var": round(abs(clean[idx]), 8),
    "cvar": round(abs(sum(tail) / len(tail)), 8) if tail else None,
    "limitations": ["VaR is not a maximum possible loss"],
  }

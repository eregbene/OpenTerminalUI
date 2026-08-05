from __future__ import annotations


def reconcile_totals(internal: float, external: float, tolerance: float = 0.01) -> dict:
  delta = round(float(internal) - float(external), 8)
  return {
    "internal": internal,
    "external": external,
    "delta": delta,
    "status": "MATCH" if abs(delta) <= tolerance else "MISMATCH",
    "tolerance": tolerance,
  }

from __future__ import annotations


def fill_quality(order_quantity: float, fills: list[dict]) -> dict:
  filled = sum(abs(float(row.get("quantity") or 0.0)) for row in fills)
  return {
    "fill_ratio": round(filled / abs(float(order_quantity)), 8) if order_quantity else 0.0,
    "partial_fill_count": max(0, len(fills) - 1),
    "fill_count": len(fills),
    "filled_quantity": round(filled, 8),
  }

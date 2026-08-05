from __future__ import annotations


def concentration_metrics(values: dict[str, float], total_gross: float) -> dict:
  gross = float(total_gross)
  ordered = sorted(((key, abs(float(value))) for key, value in values.items()), key=lambda row: row[1], reverse=True)
  return {
    "largest": {"key": ordered[0][0], "weight": ordered[0][1] / gross} if ordered and gross else None,
    "top_five_weight": sum(value for _, value in ordered[:5]) / gross if gross else 0.0,
    "sample_count": len(ordered),
  }

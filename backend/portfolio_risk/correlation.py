from __future__ import annotations

from math import sqrt


def pearson(left: list[float], right: list[float], minimum_observations: int = 3) -> dict:
  pairs = [(float(a), float(b)) for a, b in zip(left, right) if a is not None and b is not None]
  if len(pairs) < minimum_observations:
    return {"status": "INSUFFICIENT_SAMPLE", "sample_count": len(pairs), "correlation": None}
  xs = [p[0] for p in pairs]
  ys = [p[1] for p in pairs]
  mx = sum(xs) / len(xs)
  my = sum(ys) / len(ys)
  cov = sum((x - mx) * (y - my) for x, y in pairs)
  den = sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
  return {"status": "COMPLETE", "sample_count": len(pairs), "correlation": round(cov / den, 8) if den else None}

from __future__ import annotations

from backend.market_structure.models import MarketStructureSnapshot


def explain_snapshot(snapshot: MarketStructureSnapshot) -> list[str]:
    lines: list[str] = []
    if snapshot.trend:
        lines.append(f"Trend is {snapshot.trend.state} because {'; '.join(snapshot.trend.evidence)}.")
    for brk in snapshot.breaks[-5:]:
        lines.append(brk.explanation)
    for sweep in snapshot.liquidity_sweeps[-3:]:
        lines.append(f"{sweep.side} liquidity was swept at {sweep.swept_price} and reclaimed at {sweep.reclaim_price}.")
    for zone in snapshot.imbalances[-3:]:
        lines.append(f"{zone.direction} fair value gap from {zone.price_low} to {zone.price_high} is {zone.status}.")
    return lines

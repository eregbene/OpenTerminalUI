from __future__ import annotations

from decimal import Decimal
from typing import Any

# Minimum reward multiple of the stop distance -- matches MT5ExecutionService.calculate_risk_size's
# existing RISK_REWARD_TOO_LOW floor (1.5), so a selected TP1 is never rejected downstream for
# being statistically too close to be worth the risk.
MIN_REWARD_MULTIPLE = Decimal("1.5")
MAX_REWARD_MULTIPLE = Decimal("5.0")


def select_take_profit(
    *,
    direction: str,
    entry: Decimal,
    stop_loss: Decimal,
    opposing_structure_level: Decimal | None = None,
    atr: Decimal | None = None,
    tp2_multiple: Decimal = Decimal("1.5"),
    runner_multiple: Decimal = Decimal("2.2"),
) -> dict[str, Any]:
    """Structure/ATR-aware initial take-profit selection, replacing a flat fixed multiple of
    the stop distance. TP1 is bounded to [MIN_REWARD_MULTIPLE, MAX_REWARD_MULTIPLE] x the stop
    distance regardless of basis, so a selected target is always realistic for the actual risk
    taken on this trade -- never an arbitrary fixed distance unrelated to current volatility or
    market structure. TP2/runner are configurable extensions of TP1 for the Adaptive Trade
    Manager's existing staged partial-profit machinery (TP_PROGRESS_PARTIAL_PROTECT); only TP1
    is placed as the broker-side take_profit on the initial order."""
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return {"tp1": None, "tp2": None, "runner": None, "basis": "invalid_stop_distance", "reward_multiple": None}

    candidate_distances: list[tuple[Decimal, str]] = []
    if opposing_structure_level is not None:
        structure_distance = abs(opposing_structure_level - entry)
        if structure_distance > 0:
            candidate_distances.append((structure_distance, "opposing_structure"))
    if atr is not None and atr > 0:
        candidate_distances.append((atr * Decimal("2.5"), "atr_projected_move"))

    min_distance = stop_distance * MIN_REWARD_MULTIPLE
    max_distance = stop_distance * MAX_REWARD_MULTIPLE
    valid = [(dist, basis) for dist, basis in candidate_distances if dist >= min_distance]
    if valid:
        tp1_distance, basis = min(valid, key=lambda row: row[0])
    else:
        # No structure/ATR target clears the minimum reward floor -- fall back to the floor
        # itself rather than an unrealistic fixed multiple unrelated to this trade's actual stop.
        tp1_distance, basis = min_distance, "reward_floor_fallback"
    tp1_distance = min(tp1_distance, max_distance)

    tp1 = entry + tp1_distance if direction == "LONG" else entry - tp1_distance
    tp2_distance = min(tp1_distance * tp2_multiple, max_distance)
    tp2 = entry + tp2_distance if direction == "LONG" else entry - tp2_distance
    runner_distance = min(tp1_distance * runner_multiple, max_distance)
    runner = entry + runner_distance if direction == "LONG" else entry - runner_distance

    return {
        "tp1": tp1,
        "tp2": tp2,
        "runner": runner,
        "basis": basis,
        "reward_multiple": (tp1_distance / stop_distance) if stop_distance else None,
    }

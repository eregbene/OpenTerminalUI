from __future__ import annotations

from typing import Any

REGIME_RETRACEMENT_BASE = {
    "trending_up": 0.45,
    "trending_down": 0.45,
    "breakout": 0.40,
    "high_volatility": 0.35,
    "reversal": 0.25,
    "unstable_transition": 0.25,
    "ranging": 0.25,
    "low_volatility": 0.20,
    "event_driven": 0.20,
    "insufficient_data": 0.30,
}

TIMEFRAME_ALLOWANCE_ADJUSTMENT = {
    "M1": -0.05,
    "M5": -0.03,
    "M15": 0.0,
    "M30": 0.0,
    "H1": 0.03,
    "H4": 0.06,
    "D1": 0.08,
}

STOP_QUALITY_FLAGS = (
    "stop_too_tight",
    "stop_too_wide",
    "stop_inside_noise",
    "stop_not_structure_based",
    "insufficient_data",
    "acceptable",
)

WINNER_CLASSIFICATIONS = (
    "strong_continuation",
    "healthy_pullback",
    "weakening",
    "critical",
    "invalidated",
    "insufficient_data",
)


def tp_progress(direction: str, entry: float, current_price: float, tp: float | None) -> float | None:
    if tp is None or entry is None or current_price is None:
        return None
    denominator = (tp - entry) if direction == "LONG" else (entry - tp)
    if abs(denominator) < 1e-12:
        return None
    if direction == "LONG":
        return (current_price - entry) / denominator
    return (entry - current_price) / denominator


def progress_zone(progress: float | None) -> str:
    # 2026-08-18: added zone_25_60 below the prior floor (zones used to start at 60% TP
    # progress) -- user feedback from a real trade that peaked around ~30% progress and gave
    # the whole move back before ever reaching 60%: FX moves are choppy enough that waiting for
    # 60%+ before any zone-based management kicks in misses most real trades. zone_25_60 is
    # wired into both the staged partial-profit-protect logic and the ATR structure-trailing
    # logic in service.py, same as every other zone.
    if progress is None:
        return "none"
    if progress < 0.25:
        return "below_25"
    if progress < 0.60:
        return "zone_25_60"
    if progress < 0.75:
        return "zone_60_75"
    if progress < 0.85:
        return "zone_75_85"
    if progress < 0.95:
        return "zone_85_95"
    return "zone_95_plus"


def retracement_allowance(
    *,
    atr_r: float,
    regime: str,
    timeframe: str,
    min_fraction: float = 0.15,
    max_fraction: float = 0.60,
) -> float:
    base = REGIME_RETRACEMENT_BASE.get(regime, REGIME_RETRACEMENT_BASE["insufficient_data"])
    timeframe_adjustment = TIMEFRAME_ALLOWANCE_ADJUSTMENT.get(str(timeframe).upper(), 0.0)
    volatility_adjustment = min(0.10, max(0.0, (atr_r - 1.0)) * 0.05) if atr_r else 0.0
    fraction = base + timeframe_adjustment + volatility_adjustment
    return max(min_fraction, min(max_fraction, fraction))


def classify_retracement(giveback_r: float, allowance_r: float) -> str:
    if allowance_r <= 0:
        return "abnormal" if giveback_r > 0 else "normal"
    ratio = giveback_r / allowance_r
    if ratio <= 0.6:
        return "normal"
    if ratio <= 1.0:
        return "elevated"
    if ratio <= 1.5:
        return "abnormal"
    return "thesis_invalidating"


def resolve_profit_lock_floor(
    *,
    max_tp_progress: float,
    max_achieved_r: float,
    regime: str,
    atr_r: float,
) -> dict[str, Any]:
    if max_tp_progress < 0.80 or max_achieved_r <= 0:
        return {"triggered": False, "protect_fraction": 0.0, "floor_r": None, "band": None}
    trending = regime in {"trending_up", "trending_down", "breakout"}
    if max_tp_progress >= 0.90:
        band = (0.70, 0.85)
    else:
        band = (0.50, 0.70)
    depth = min(1.0, (max_tp_progress - (0.90 if max_tp_progress >= 0.90 else 0.80)) / 0.10)
    protect_fraction = band[0] + (band[1] - band[0]) * depth
    if trending:
        protect_fraction = max(band[0], protect_fraction - 0.05)
    if atr_r and atr_r > 1.5:
        protect_fraction = min(band[1], protect_fraction + 0.05)
    floor_r = max_achieved_r * protect_fraction
    return {"triggered": True, "protect_fraction": protect_fraction, "floor_r": floor_r, "band": band}


def classify_winner_preservation(evidence: dict[str, Any]) -> dict[str, Any]:
    opposing_candles = int(evidence.get("opposing_candles") or 0)
    retracement_state = str(evidence.get("retracement_state") or "normal")
    regime = str(evidence.get("regime") or "insufficient_data")
    direction = str(evidence.get("direction") or "LONG")
    trend = str(evidence.get("higher_timeframe_trend") or "NEUTRAL").upper()
    candles_held = evidence.get("candles_held")

    if candles_held is None:
        return {"classification": "insufficient_data", "reasons": ["no_candle_history"]}

    if retracement_state == "thesis_invalidating" or opposing_candles >= 3:
        return {"classification": "invalidated", "reasons": ["thesis_invalidating_retracement_or_sustained_opposing_candles"]}

    if opposing_candles >= 2 and retracement_state == "abnormal":
        return {"classification": "critical", "reasons": ["multiple_opposing_candles_with_abnormal_giveback"]}

    if retracement_state == "abnormal" or opposing_candles >= 2:
        return {"classification": "weakening", "reasons": ["abnormal_giveback_or_opposing_candle_confirmation"]}

    if trend == "NEUTRAL":
        trend_supports = (direction == "LONG" and regime == "trending_up") or (direction == "SHORT" and regime == "trending_down") or regime == "breakout"
    else:
        trend_supports = (direction == "LONG" and trend == "BULLISH") or (direction == "SHORT" and trend == "BEARISH")
    if retracement_state in {"normal", "elevated"} and trend_supports and regime in {"trending_up", "trending_down", "breakout"}:
        return {"classification": "strong_continuation", "reasons": ["trend_and_regime_supportive_normal_retracement"]}

    if retracement_state in {"normal", "elevated"}:
        return {"classification": "healthy_pullback", "reasons": ["retracement_within_normal_or_elevated_allowance"]}

    return {"classification": "insufficient_data", "reasons": ["ambiguous_evidence"]}


def classify_stop_quality(
    *,
    sl_distance: float,
    atr: float | None,
    spread: float | None,
    structure_distance: float | None,
    broker_min_stop: float | None,
    min_atr_mult: float = 0.8,
    max_atr_mult: float = 3.5,
    min_spread_ratio: float = 3.0,
) -> dict[str, Any]:
    if sl_distance is None or sl_distance <= 0 or not atr or atr <= 0:
        return {"classification": "insufficient_data", "flags": ["insufficient_data"], "sl_atr_multiple": None, "spread_pct_of_sl": None}
    atr_multiple = sl_distance / atr
    spread_pct = (spread / sl_distance) if spread else None
    flags: list[str] = []
    if atr_multiple < min_atr_mult or (broker_min_stop and sl_distance < broker_min_stop * 1.2):
        flags.append("stop_too_tight")
    if atr_multiple > max_atr_mult:
        flags.append("stop_too_wide")
    if spread and sl_distance < spread * min_spread_ratio:
        flags.append("stop_inside_noise")
    if structure_distance is not None and structure_distance > 0:
        deviation = abs(sl_distance - structure_distance) / structure_distance
        if deviation > 0.6:
            flags.append("stop_not_structure_based")
    classification = flags[0] if flags else "acceptable"
    return {"classification": classification, "flags": flags or ["acceptable"], "sl_atr_multiple": atr_multiple, "spread_pct_of_sl": spread_pct}


STOP_QUALITY_V2_CLASSIFICATIONS = ("VALID", "TOO_TIGHT", "TOO_WIDE", "INVALID_STRUCTURE", "INVALID_BROKER_DISTANCE", "UNAFFORDABLE_RISK")


def classify_stop_quality_v2(
    *,
    sl_distance: float | None,
    atr: float | None,
    spread: float | None,
    structure_distance: float | None,
    broker_min_stop_distance: float | None,
    effective_risk_budget_usd: float | None = None,
    projected_monetary_loss_usd: float | None = None,
    min_atr_mult: float = 0.8,
    max_atr_mult: float = 3.5,
    min_spread_ratio: float = 3.0,
) -> dict[str, Any]:
    """Returns one of STOP_QUALITY_V2_CLASSIFICATIONS. Reuses the same distance/ATR/spread/
    structure math as classify_stop_quality() but (a) actually checks the real broker minimum
    stop distance instead of silently accepting broker_min_stop=None, and (b) adds an
    affordability check against the effective risk budget -- a structurally valid stop that
    would still cost more than the account can risk is UNAFFORDABLE_RISK, not VALID."""
    reasons: list[str] = []
    if sl_distance is None or sl_distance <= 0:
        return {"classification": "INVALID_STRUCTURE", "reasons": ["missing_or_zero_sl_distance"], "sl_atr_multiple": None, "spread_pct_of_sl": None}
    if broker_min_stop_distance and sl_distance < broker_min_stop_distance:
        return {"classification": "INVALID_BROKER_DISTANCE", "reasons": ["below_broker_minimum_stop_distance"], "sl_atr_multiple": None, "spread_pct_of_sl": None, "broker_min_stop_distance": broker_min_stop_distance}
    if effective_risk_budget_usd is not None and projected_monetary_loss_usd is not None and projected_monetary_loss_usd > effective_risk_budget_usd * 1.001:
        return {"classification": "UNAFFORDABLE_RISK", "reasons": ["projected_monetary_loss_exceeds_effective_risk_budget"], "sl_atr_multiple": None, "spread_pct_of_sl": None}
    if spread and sl_distance < spread * min_spread_ratio:
        reasons.append("inside_spread_noise_floor")
    atr_multiple = (sl_distance / atr) if atr and atr > 0 else None
    spread_pct = (spread / sl_distance) if spread else None
    if atr_multiple is not None:
        if atr_multiple < min_atr_mult or reasons:
            return {"classification": "TOO_TIGHT", "reasons": reasons or ["below_minimum_atr_multiple"], "sl_atr_multiple": atr_multiple, "spread_pct_of_sl": spread_pct}
        if atr_multiple > max_atr_mult:
            return {"classification": "TOO_WIDE", "reasons": ["above_maximum_atr_multiple"], "sl_atr_multiple": atr_multiple, "spread_pct_of_sl": spread_pct}
    elif reasons:
        return {"classification": "TOO_TIGHT", "reasons": reasons, "sl_atr_multiple": None, "spread_pct_of_sl": spread_pct}
    if structure_distance is not None and structure_distance > 0:
        deviation = abs(sl_distance - structure_distance) / structure_distance
        if deviation > 0.6:
            return {"classification": "INVALID_STRUCTURE", "reasons": ["stop_not_structure_based"], "sl_atr_multiple": atr_multiple, "spread_pct_of_sl": spread_pct}
    return {"classification": "VALID", "reasons": ["within_acceptable_bounds"], "sl_atr_multiple": atr_multiple, "spread_pct_of_sl": spread_pct}


def construct_dynamic_stop(
    direction: str,
    entry: float,
    structure_level: float | None,
    atr: float | None,
    spread: float | None,
    *,
    min_atr_mult: float = 1.0,
    max_atr_mult: float = 3.0,
    min_structure_buffer: float = 0.0,
    max_stop_distance: float | None = None,
    min_spread_ratio: float = 3.0,
    broker_min_stop_distance: float | None = None,
) -> float | None:
    """`broker_min_stop_distance` (price units, i.e. symbol_info.trade_stops_level *
    symbol_info.point): when provided and positive, the final distance is never allowed below
    it -- the broker will reject (or silently adjust) an order whose stop sits closer to entry
    than its own minimum stop level, and this must be caught here, before sizing/order
    submission, not discovered via an order_send rejection. Optional and backward-compatible:
    omitted (None, the default) reproduces byte-identical behavior for every existing caller
    that doesn't pass it."""
    if entry is None or atr is None or atr <= 0 or min_atr_mult <= 0 or max_atr_mult <= 0 or min_atr_mult > max_atr_mult:
        return None
    spread_value = spread or 0.0
    buffer = max(atr * 0.15, spread_value * 2.0, min_structure_buffer)
    if structure_level is not None:
        structure_span = abs(entry - structure_level)
        if structure_span <= 0:
            return None
        raw_distance = structure_span + buffer
    else:
        raw_distance = atr * max(min_atr_mult, 1.0)
    min_distance = atr * min_atr_mult
    if broker_min_stop_distance is not None and broker_min_stop_distance > 0:
        min_distance = max(min_distance, broker_min_stop_distance)
    max_distance = atr * max_atr_mult
    if max_stop_distance is not None:
        max_distance = min(max_distance, max_stop_distance)
    if min_distance > max_distance:
        return None
    distance = max(min_distance, min(raw_distance, max_distance))
    if distance <= 0 or distance < spread_value * min_spread_ratio:
        return None
    return entry - distance if direction == "LONG" else entry + distance

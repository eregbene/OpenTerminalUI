from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Literal


ActionType = Literal["HOLD", "MOVE_SL_BREAKEVEN", "PARTIAL_CLOSE", "CLOSE"]


@dataclass(frozen=True)
class V3AdaptiveState:
    strategy_id: str
    management_model: str
    entry: float
    stop: float
    target: float
    current_price: float
    direction: Literal["LONG", "SHORT"]
    partials_taken: int = 0
    stop_at_breakeven: bool = False
    target_side_liquidity_taken: bool = False


@dataclass(frozen=True)
class V3AdaptiveDecision:
    action: ActionType
    reason: str
    requested_sl: float | None = None
    partial_fraction: float | None = None


@dataclass(frozen=True)
class V3AdaptiveBucketStats:
    trades: int
    win_rate: float | None
    expectancy: float | None
    profit_factor: float | None
    max_losing_streak: int


@dataclass(frozen=True)
class V3AdaptiveEntryDecision:
    allow_entry: bool
    reason: str
    risk_mode: str | None = None
    profile_id: str | None = None


@dataclass(frozen=True)
class V3AdaptiveRoutingBucket:
    strategy_id: str
    symbol: str
    score: float
    windows: tuple[str, ...]


@dataclass(frozen=True)
class V3NextSessionProfile:
    profile_id: str
    next_trading_day_utc_date: str
    risk_mode: str
    allowed_buckets: tuple[V3AdaptiveRoutingBucket, ...]
    generic_detector_routing_count: int = 0


@dataclass(frozen=True)
class V3BrokerRules:
    digits: int = 5
    volume_min: Decimal = Decimal("0.01")
    volume_max: Decimal = Decimal("100")
    volume_step: Decimal = Decimal("0.01")


def load_v3_next_session_profile(path: str | Path | None = None) -> V3NextSessionProfile | None:
    raw_path = path or os.getenv("BSI_V3_NEXT_SESSION_PROFILE_PATH")
    if not raw_path:
        return None
    profile_path = Path(raw_path)
    if not profile_path.exists():
        return None
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    buckets = tuple(
        V3AdaptiveRoutingBucket(
            strategy_id=str(row["strategy_id"]),
            symbol=str(row["symbol"]).upper(),
            score=float(row.get("score") or 0.0),
            windows=tuple(str(item) for item in row.get("windows", [])),
        )
        for row in payload.get("allowed_buckets", [])
    )
    return V3NextSessionProfile(
        profile_id=str(payload["profile_id"]),
        next_trading_day_utc_date=str(payload["next_trading_day_utc_date"]),
        risk_mode=str(payload.get("risk_mode") or "NORMAL"),
        allowed_buckets=buckets,
        generic_detector_routing_count=int(payload.get("risk_rules", {}).get("generic_detector_routing_count") or payload.get("generic_detector_routing_count") or 0),
    )


def allow_v3_demo_entry_from_profile(
    profile: V3NextSessionProfile | None,
    *,
    strategy_id: str,
    symbol: str,
) -> V3AdaptiveEntryDecision:
    if profile is None:
        return V3AdaptiveEntryDecision(False, "v3_next_session_profile_missing")
    if profile.generic_detector_routing_count:
        return V3AdaptiveEntryDecision(False, "v3_profile_contains_generic_detector_routes", risk_mode=profile.risk_mode, profile_id=profile.profile_id)
    if profile.risk_mode == "PAUSE_NEW_V3_ENTRIES":
        return V3AdaptiveEntryDecision(False, "v3_profile_risk_mode_paused", risk_mode=profile.risk_mode, profile_id=profile.profile_id)
    if profile.risk_mode == "REDUCE_RISK_OR_NEW_ENTRIES":
        return V3AdaptiveEntryDecision(False, "v3_profile_risk_mode_reduce_or_pause", risk_mode=profile.risk_mode, profile_id=profile.profile_id)
    wanted = (strategy_id, symbol.upper())
    allowed = {(row.strategy_id, row.symbol) for row in profile.allowed_buckets}
    if wanted not in allowed:
        return V3AdaptiveEntryDecision(False, "v3_profile_bucket_not_allowed_next_session", risk_mode=profile.risk_mode, profile_id=profile.profile_id)
    return V3AdaptiveEntryDecision(True, "v3_profile_bucket_allowed_next_session", risk_mode=profile.risk_mode, profile_id=profile.profile_id)


def v3_stop_improves_risk(
    *,
    direction: Literal["LONG", "SHORT"],
    current_sl: float | None,
    requested_sl: float,
    entry: float,
) -> bool:
    if current_sl is None:
        return True
    if direction == "LONG":
        return requested_sl > current_sl
    return requested_sl < current_sl


def v3_broker_safe_sl(
    *,
    direction: Literal["LONG", "SHORT"],
    current_sl: float | None,
    requested_sl: float,
    entry: float,
    rules: V3BrokerRules,
) -> float | None:
    rounded = round(float(requested_sl), int(rules.digits))
    if not v3_stop_improves_risk(direction=direction, current_sl=current_sl, requested_sl=rounded, entry=entry):
        return None
    return rounded


def v3_broker_safe_partial_volume(
    *,
    current_volume: float,
    fraction: float,
    rules: V3BrokerRules,
) -> Decimal:
    raw = Decimal(str(max(0.0, current_volume))) * Decimal(str(max(0.0, min(1.0, fraction))))
    step = rules.volume_step if rules.volume_step > 0 else Decimal("0.01")
    rounded = (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step
    bounded = min(max(rounded, Decimal("0")), rules.volume_max)
    if bounded < rules.volume_min:
        return Decimal("0")
    remaining = Decimal(str(current_volume)) - bounded
    if Decimal("0") < remaining < rules.volume_min:
        bounded = Decimal(str(current_volume))
    return bounded


def allow_v3_demo_entry_from_bucket(
    stats: V3AdaptiveBucketStats | None,
    *,
    min_trades: int = 10,
    min_expectancy: float = 0.02,
    min_profit_factor: float = 1.05,
    max_losing_streak: int = 8,
) -> V3AdaptiveEntryDecision:
    """Evidence gate for V3 demo entries.

    The manager must not only move stops after entry. It also has to suppress
    strategy/symbol buckets that are repeatedly negative in replay evidence.
    """

    if stats is None:
        return V3AdaptiveEntryDecision(False, "v3_no_adaptive_bucket_evidence")
    if stats.trades < min_trades:
        return V3AdaptiveEntryDecision(False, "v3_bucket_sample_too_small")
    if stats.expectancy is None or stats.expectancy < min_expectancy:
        return V3AdaptiveEntryDecision(False, "v3_bucket_expectancy_below_threshold")
    if stats.profit_factor is None or stats.profit_factor < min_profit_factor:
        return V3AdaptiveEntryDecision(False, "v3_bucket_profit_factor_below_threshold")
    if stats.max_losing_streak > max_losing_streak:
        return V3AdaptiveEntryDecision(False, "v3_bucket_losing_streak_too_high")
    return V3AdaptiveEntryDecision(True, "v3_bucket_adaptive_evidence_pass")


def _r_multiple(state: V3AdaptiveState) -> float:
    risk = abs(state.entry - state.stop)
    if risk <= 0:
        return 0.0
    sign = 1 if state.direction == "LONG" else -1
    return sign * (state.current_price - state.entry) / risk


def select_v3_demo_management_action(state: V3AdaptiveState) -> V3AdaptiveDecision:
    """Strategy-aware Faiz V3 demo policy.

    This module is broker-neutral: it returns the action a demo execution layer may submit,
    but it does not mutate MT5/cTrader positions by itself.
    """

    r_now = _r_multiple(state)
    model = state.management_model

    if r_now <= -1.0:
        return V3AdaptiveDecision("HOLD", "v3_stop_should_be_broker_side_not_adaptive_close")

    if model in {"MENTOR_FIXED_1R_BE_2R_TARGET", "MENTOR_BE_AT_1R"}:
        if r_now >= 1.0 and not state.stop_at_breakeven:
            return V3AdaptiveDecision("MOVE_SL_BREAKEVEN", "v3_fixed_r_be_at_1r", requested_sl=state.entry)
        return V3AdaptiveDecision("HOLD", "v3_fixed_r_wait_for_1r_or_target")

    if model in {"MENTOR_PARTIAL_AT_POI_OR_2R", "MENTOR_FINAL_TARGET_LIQUIDITY"}:
        if r_now >= 2.0 and state.partials_taken == 0:
            return V3AdaptiveDecision("PARTIAL_CLOSE", "v3_partial_at_2r_or_poi", partial_fraction=0.5)
        if r_now >= 1.0 and not state.stop_at_breakeven:
            return V3AdaptiveDecision("MOVE_SL_BREAKEVEN", "v3_protect_after_1r", requested_sl=state.entry)
        return V3AdaptiveDecision("HOLD", "v3_wait_for_poi_or_liquidity_target")

    if model == "MENTOR_IFVG_CLOSEST_LIQUIDITY_BE":
        if state.target_side_liquidity_taken and not state.stop_at_breakeven:
            return V3AdaptiveDecision("MOVE_SL_BREAKEVEN", "v3_ifvg_closest_liquidity_taken", requested_sl=state.entry)
        return V3AdaptiveDecision("HOLD", "v3_ifvg_wait_for_closest_liquidity")

    if model in {
        "MENTOR_TURTLE_RANGE_PARTIAL_BE_AT_0_5",
        "MENTOR_CANDLE_RANGE_PARTIAL_BE_TARGET_OPPOSITE_SIDE",
        "MENTOR_1H_CRD_PARTIAL_AT_50_PERCENT_RANGE",
        "MENTOR_ENIGMA_PARTIAL_BE_0_5_TARGET_0_79_OR_1_0",
    }:
        if r_now >= 0.5 and state.partials_taken == 0:
            return V3AdaptiveDecision("PARTIAL_CLOSE", "v3_range_partial_at_half_range", partial_fraction=0.4)
        if r_now >= 0.5 and not state.stop_at_breakeven:
            return V3AdaptiveDecision("MOVE_SL_BREAKEVEN", "v3_range_be_after_half_range", requested_sl=state.entry)
        return V3AdaptiveDecision("HOLD", "v3_range_wait_for_half_range")

    if model == "MENTOR_SESSION_SMT_BE_1R_OR_STRONG_BIAS_1_5R":
        if r_now >= 1.0 and not state.stop_at_breakeven:
            return V3AdaptiveDecision("MOVE_SL_BREAKEVEN", "v3_smt_session_be_at_1r", requested_sl=state.entry)
        return V3AdaptiveDecision("HOLD", "v3_smt_session_wait_for_1r")

    return V3AdaptiveDecision("HOLD", "v3_unknown_management_model_hold")

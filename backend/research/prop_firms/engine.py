from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.consensus import build_consensus
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.strategies import evaluate_strategies
from backend.research.prop_firms.models import ChallengeAccountState, ExecutionScenario, InternalSafetyProfile, PropFirmProfile

PIP_VALUE_PER_STANDARD_LOT = 10.0


def initial_account_state(profile: PropFirmProfile) -> ChallengeAccountState:
    initial = float(profile.account_size)
    return ChallengeAccountState(
        initial_balance=initial,
        current_balance=initial,
        current_equity=initial,
        intraday_high_equity=initial,
        previous_day_balance=initial,
        previous_day_equity=initial,
        daily_loss_allowance=initial * profile.maximum_daily_loss_percent / 100,
        total_loss_allowance=initial * profile.maximum_total_loss_percent / 100,
        profit_target=initial * profile.profit_target_percent / 100,
        phase_state="ACTIVE",
    )


def daily_loss_amount(state: ChallengeAccountState, profile: PropFirmProfile) -> float:
    if profile.daily_loss_calculation_method == "balance_only_from_daily_start":
        return max(0.0, state.previous_day_balance - state.current_balance)
    return max(0.0, state.previous_day_equity - state.current_equity, -state.daily_closed_pnl + max(0.0, -state.floating_pnl))


def total_loss_amount(state: ChallengeAccountState, profile: PropFirmProfile) -> float:
    if profile.static_or_trailing_drawdown.upper() == "TRAILING":
        return max(0.0, state.intraday_high_equity - state.current_equity)
    return max(0.0, state.initial_balance - state.current_equity)


def best_day_share(day_pnls: dict[str, float]) -> float:
    positives = [value for value in day_pnls.values() if value > 0]
    total = sum(positives)
    return 0.0 if total <= 0 else max(positives) / total * 100


def consistency_status(state: ChallengeAccountState, profile: PropFirmProfile) -> str:
    if profile.best_day_consistency_percent is not None and best_day_share(state.day_pnls) > profile.best_day_consistency_percent:
        return "CONSISTENCY_NOT_MET"
    if state.trading_days < profile.minimum_trading_days:
        return "MINIMUM_DAYS_NOT_MET"
    if state.profitable_days < profile.minimum_profitable_days:
        return "MINIMUM_PROFITABLE_DAYS_NOT_MET"
    return "OK"


def apply_trade(
    state: ChallengeAccountState,
    profile: PropFirmProfile,
    internal: InternalSafetyProfile,
    trade: dict[str, Any],
    scenario: ExecutionScenario,
) -> ChallengeAccountState:
    state = replace(state)
    day = str(trade["day"])
    gross_r = float(trade["r_multiple"])
    risk_amount = state.initial_balance * float(trade["risk_percent"]) / 100
    cost = _execution_cost_usd(float(trade["risk_percent"]), state.initial_balance, scenario)
    pnl = risk_amount * gross_r - cost
    state.commissions += scenario.commission_per_lot * max(0.01, risk_amount / 1000)
    state.slippage += (scenario.slippage_pips + (scenario.stop_slippage_pips if gross_r < 0 else 0)) * PIP_VALUE_PER_STANDARD_LOT * max(0.01, risk_amount / 1000)
    state.swaps += scenario.swap_per_day if trade.get("overnight") else 0.0
    state.current_balance += pnl
    state.current_equity = state.current_balance
    state.intraday_high_equity = max(state.intraday_high_equity, state.current_equity)
    state.daily_closed_pnl += pnl
    state.weekly_pnl += pnl
    state.day_pnls[day] = state.day_pnls.get(day, 0.0) + pnl
    state.trading_days = len(state.day_pnls)
    threshold = state.initial_balance * profile.profitable_day_threshold_percent / 100
    state.profitable_days = len([value for value in state.day_pnls.values() if value >= threshold and value > 0])
    state.best_trading_day = max(state.day_pnls.values()) if state.day_pnls else 0.0
    state.consecutive_losses = state.consecutive_losses + 1 if pnl < 0 else 0
    state.phase_state = _state_after_trade(state, profile, internal)
    if state.phase_state in {"PHASE_PASSED", "TOTAL_DRAWDOWN_BREACH", "DAILY_DRAWDOWN_BREACH", "DAILY_LOCKED", "WEEKLY_LOCKED"}:
        state.phase_completion_timestamp = str(trade["timestamp"])
    return state


def simulate_profile(
    *,
    profile: PropFirmProfile,
    internal: InternalSafetyProfile,
    candidates: list[dict[str, Any]],
    risk_percent: float,
    trade_cap: int,
    scenario: ExecutionScenario,
    policy: str,
) -> dict[str, Any]:
    state = initial_account_state(profile)
    accepted: list[dict[str, Any]] = []
    rejected = 0
    by_day: Counter[str] = Counter()
    overlap_attempts = 0
    for row in candidates:
        day = str(row["day"])
        if by_day[day] >= min(trade_cap, internal.max_trades_per_day):
            rejected += 1
            continue
        if state.phase_state not in {"ACTIVE", "WAITING_FOR_MINIMUM_DAYS", "PROFIT_TARGET_REACHED"}:
            break
        if state.consecutive_losses >= internal.max_consecutive_losses:
            state.phase_state = "DAILY_LOCKED"
            state.breach_reason = "INTERNAL_CONSECUTIVE_LOSS_STOP"
            break
        if state.weekly_pnl <= -(state.initial_balance * internal.max_weekly_loss_percent / 100):
            state.phase_state = "WEEKLY_LOCKED"
            state.breach_reason = "INTERNAL_WEEKLY_LOSS_STOP"
            break
        if _deterministic_reject(row, scenario):
            rejected += 1
            continue
        trade = dict(row)
        trade["risk_percent"] = min(risk_percent, internal.risk_per_trade_percent)
        state = apply_trade(state, profile, internal, trade, scenario)
        by_day[day] += 1
        accepted.append(trade)
        if by_day[day] > 1:
            overlap_attempts += 1
    final_consistency = consistency_status(state, profile)
    if state.phase_state == "PROFIT_TARGET_REACHED":
        state.phase_state = "PHASE_PASSED" if final_consistency == "OK" else "WAITING_FOR_MINIMUM_DAYS"
        state.breach_reason = None if final_consistency == "OK" else final_consistency
    return {
        "profile_id": profile.profile_id,
        "risk_percent": risk_percent,
        "trade_frequency_cap": trade_cap,
        "execution_scenario": scenario.name,
        "policy": policy,
        "state": state.model_dump(),
        "accepted_trades": accepted,
        "rejected_trades": rejected,
        "position_overlap_attempts": overlap_attempts,
        "status": state.phase_state,
        "profit": state.current_balance - state.initial_balance,
        "max_drawdown_percent": total_loss_amount(state, profile) / state.initial_balance * 100,
        "best_day_consistency_percent": best_day_share(state.day_pnls),
    }


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(attempts)
    passed = [row for row in attempts if row["status"] == "PHASE_PASSED"]
    failed = [row for row in attempts if "BREACH" in row["status"] or row["status"] in {"PHASE_FAILED", "CONSISTENCY_NOT_MET", "INACTIVITY_BREACH"}]
    durations = [row["state"]["trading_days"] for row in passed]
    trades_to_pass = [len(row["accepted_trades"]) for row in passed]
    return {
        "total_attempts": total,
        "passed": len(passed),
        "failed": len(failed),
        "unfinished": max(0, total - len(passed) - len(failed)),
        "pass_rate": len(passed) / total if total else 0.0,
        "breach_rate": len(failed) / total if total else 0.0,
        "median_days_to_pass": median(durations) if durations else None,
        "average_days_to_pass": mean(durations) if durations else None,
        "p25_days_to_pass": _percentile(durations, 0.25),
        "p75_days_to_pass": _percentile(durations, 0.75),
        "median_trades_to_pass": median(trades_to_pass) if trades_to_pass else None,
        "average_trades_to_pass": mean(trades_to_pass) if trades_to_pass else None,
        "maximum_drawdown_before_passing": max((row["max_drawdown_percent"] for row in attempts), default=0.0),
        "daily_breach_frequency": len([row for row in attempts if row["status"] == "DAILY_DRAWDOWN_BREACH"]) / total if total else 0.0,
        "total_breach_frequency": len([row for row in attempts if row["status"] == "TOTAL_DRAWDOWN_BREACH"]) / total if total else 0.0,
        "consistency_failures": len([row for row in attempts if row["state"].get("breach_reason") in {"CONSISTENCY_NOT_MET", "MINIMUM_DAYS_NOT_MET", "MINIMUM_PROFITABLE_DAYS_NOT_MET"}]),
    }


def trade_frequency(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")) for row in candidates]
    by_day = Counter(row["day"] for row in candidates)
    deltas = [(b - a).total_seconds() / 60 for a, b in zip(timestamps, timestamps[1:])]
    no_trade_days = _max_no_trade_days(sorted(by_day))
    return {
        "candidate_candles": len(candidates),
        "simulated_accepted_trades": len(candidates),
        "average_trades_per_day": mean(by_day.values()) if by_day else 0.0,
        "average_trades_per_week": len(candidates) / max(1, len(by_day) / 5),
        "average_trades_per_month": len(candidates) / max(1, len(by_day) / 21),
        "median_time_between_trades_minutes": median(deltas) if deltas else None,
        "maximum_consecutive_days_without_trade": no_trade_days,
        "maximum_trades_on_one_day": max(by_day.values()) if by_day else 0,
        "trades_by_session": dict(Counter(row.get("session", "unknown") for row in candidates)),
        "trades_by_weekday": dict(Counter(row.get("weekday", "unknown") for row in candidates)),
        "trades_by_regime": dict(Counter(row.get("regime", "unknown") for row in candidates)),
        "trades_by_strategy": dict(Counter(row.get("strategy", "consensus") for row in candidates)),
    }


def monte_carlo(attempt: dict[str, Any], *, runs: int, seed: int) -> dict[str, Any]:
    trades = list(attempt.get("accepted_trades") or [])
    if not trades:
        return {"runs": runs, "seed": seed, "status": "INSUFFICIENT_DATA", "pass_probability": 0.0, "daily_breach_probability": 0.0, "total_breach_probability": 0.0}
    rng = random.Random(seed)
    profits = []
    losing_streaks = []
    for _ in range(runs):
        sample = [rng.choice(trades) for _ in trades]
        pnl = sum(float(row["r_multiple"]) for row in sample)
        profits.append(pnl)
        losing_streaks.append(_longest_losing_streak(sample))
    return {
        "runs": runs,
        "seed": seed,
        "pass_probability": len([value for value in profits if value > 0]) / runs,
        "daily_breach_probability": len([value for value in profits if value < -2]) / runs,
        "total_breach_probability": len([value for value in profits if value < -4]) / runs,
        "internal_stop_probability": len([value for value in losing_streaks if value >= 2]) / runs,
        "expected_time_to_pass_days": attempt["state"].get("trading_days"),
        "p95_drawdown_proxy_r": abs(_percentile([min(0, value) for value in profits], 0.05) or 0),
        "expected_longest_losing_streak": mean(losing_streaks),
    }


def compatibility(summary: dict[str, Any], profile: PropFirmProfile) -> dict[str, Any]:
    pass_rate = float(summary.get("pass_rate") or 0)
    breach_rate = float(summary.get("breach_rate") or 0)
    if profile.automated_trading_allowed == "PROGRAM_VERIFICATION_REQUIRED":
        classification = "PROGRAM_PERMISSION_REVIEW_REQUIRED"
    elif summary.get("total_attempts", 0) < 3:
        classification = "INSUFFICIENT_DATA"
    elif pass_rate >= 0.6 and breach_rate <= 0.05:
        classification = "HIGHLY_COMPATIBLE"
    elif pass_rate >= 0.35 and breach_rate <= 0.1:
        classification = "COMPATIBLE"
    elif pass_rate > 0:
        classification = "MARGINAL"
    else:
        classification = "INCOMPATIBLE"
    return {"profile_id": profile.profile_id, "score": max(0.0, min(100.0, pass_rate * 100 - breach_rate * 100)), "classification": classification, "automation_policy_status": profile.automated_trading_allowed}


def candidates_from_candles(candles: list[dict[str, Any]], *, config: AITradingConfig) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    warmup = 220 if len(candles) >= 220 else 60
    for idx in range(warmup, len(candles) + 1):
        window = candles[:idx]
        ctx = build_market_context(window[-warmup:], symbol="EURUSD", timeframe="15m", spread=0.8)
        outputs = evaluate_strategies(ctx)
        consensus = build_consensus(outputs).model_dump()
        direction_score = max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0))
        if consensus.get("recommended_direction") not in {"LONG", "SHORT"}:
            continue
        if float(consensus.get("agreement_score") or 0) < config.consensus_threshold or direction_score < config.min_directional_score:
            continue
        if int(consensus.get("eligible_strategy_count") or 0) < config.min_eligible_strategies:
            continue
        ts = datetime.fromtimestamp(int(window[-1]["t"]), tz=timezone.utc)
        best = next((row for row in outputs if row.decision == consensus["recommended_direction"] and row.entry_conditions_met), None)
        if not best:
            continue
        out.append(
            {
                "candidate_id": hashlib.sha256(f"{ts.isoformat()}:{best.strategy}:{best.decision}".encode()).hexdigest()[:16],
                "timestamp": ts.isoformat(),
                "day": ts.date().isoformat(),
                "weekday": ts.strftime("%A"),
                "session": ctx.session,
                "regime": ctx.market_regime,
                "strategy": best.strategy,
                "direction": best.decision,
                "confidence": best.confidence,
                "risk_reward": best.risk_reward,
                "r_multiple": _deterministic_r_multiple(window[-8:], best.decision, best.risk_reward),
                "overnight": False,
            }
        )
    return out


def _state_after_trade(state: ChallengeAccountState, profile: PropFirmProfile, internal: InternalSafetyProfile) -> str:
    if daily_loss_amount(state, profile) >= state.daily_loss_allowance:
        state.breach_reason = "FIRM_DAILY_DRAWDOWN"
        return "DAILY_DRAWDOWN_BREACH"
    if total_loss_amount(state, profile) >= state.total_loss_allowance:
        state.breach_reason = "FIRM_TOTAL_DRAWDOWN"
        return "TOTAL_DRAWDOWN_BREACH"
    if total_loss_amount(state, profile) >= state.initial_balance * internal.max_total_drawdown_percent / 100:
        state.breach_reason = "INTERNAL_TOTAL_DRAWDOWN_STOP"
        return "PHASE_FAILED"
    if state.current_balance - state.initial_balance >= state.profit_target:
        return "PROFIT_TARGET_REACHED"
    return "ACTIVE"


def _execution_cost_usd(risk_percent: float, balance: float, scenario: ExecutionScenario) -> float:
    risk_amount = balance * risk_percent / 100
    lot_proxy = max(0.01, risk_amount / 1000)
    return (scenario.spread_pips + scenario.slippage_pips) * PIP_VALUE_PER_STANDARD_LOT * lot_proxy + scenario.commission_per_lot * lot_proxy


def _deterministic_reject(row: dict[str, Any], scenario: ExecutionScenario) -> bool:
    score = int(row["candidate_id"][:4], 16) / 0xFFFF
    return score < scenario.rejected_entry_probability


def _deterministic_r_multiple(candles: list[dict[str, Any]], direction: str, risk_reward: float) -> float:
    start = float(candles[0]["c"])
    end = float(candles[-1]["c"])
    move = end - start
    winner = move > 0 if direction == "LONG" else move < 0
    return min(float(risk_reward or 1.5), 2.2) if winner else -1.0


def _percentile(values: list[float | int], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * q)
    return float(ordered[idx])


def _max_no_trade_days(days: list[str]) -> int:
    if len(days) < 2:
        return 0
    parsed = [datetime.fromisoformat(day).date() for day in days]
    return max((b - a).days - 1 for a, b in zip(parsed, parsed[1:]))


def _longest_losing_streak(trades: list[dict[str, Any]]) -> int:
    longest = current = 0
    for trade in trades:
        if float(trade.get("r_multiple") or 0) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest

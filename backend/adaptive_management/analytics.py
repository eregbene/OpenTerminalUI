"""Adaptive Trade Manager Validation & Performance Analytics layer -- Parts 3-14.

Read-only analytics over AdaptivePositionStateORM (existing) joined with
AdaptivePositionBaselineORM, AdaptiveManagementEventORM, AdaptiveManagementActionORM (existing)
and AdaptiveManagerCounterfactualORM. Nothing here writes to the trading/management engine, a
weight, a threshold, or a rule. This is evidence-gathering only, mirroring
backend/brokers/mt5/confidence_calibration.py's role for the confidence layer.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptiveManagementActionORM, AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.adaptive_management.outcome_resolver import AdaptiveManagerOutcomeResolver
from backend.shared.db import SessionLocal

_HOLD_ACTION_TYPES = {"HOLD", "HOLD_WITH_GIVEBACK_RISK"}
_TRAILING_ACTION_TYPES = {"TRAIL_STOP", "MOVE_SL_TO_REDUCED_RISK"}
CONFIDENCE_BANDS = ("70-74", "75-79", "80-84", "85-89", "90+")
WINNER_BUCKETS = (0.5, 1.0, 1.5, 2.0, 3.0)


def sample_label(n: int) -> str:
    """Reporting labels only (Part 15) -- not hard scientific guarantees."""
    if n < 20:
        return "insufficient"
    if n < 50:
        return "low_confidence"
    if n < 100:
        return "preliminary"
    return "increasingly_useful"


def _session_label(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    hour = dt.hour
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 21:
        return "NEW_YORK"
    return "ASIA"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _band_bucket(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 70:
        return None
    if score < 75:
        return "70-74"
    if score < 80:
        return "75-79"
    if score < 85:
        return "80-84"
    if score < 90:
        return "85-89"
    return "90+"


def _capture_ratio(realized_r: float | None, mfe_r: float | None) -> float | None:
    if realized_r is None or mfe_r is None or mfe_r <= 0:
        return None  # Do not present a ratio for trades that never had positive excursion.
    return round(realized_r / mfe_r, 4)


def _giveback_r(realized_r: float | None, mfe_r: float | None) -> float | None:
    if realized_r is None or mfe_r is None or mfe_r <= 0:
        return None
    return round(mfe_r - realized_r, 4)


def _derive_exit_category(*, exit_reason: str | None, final_sl: float | None, original_sl: float | None, final_tp: float | None, original_tp: float | None, last_action_type: str | None) -> str:
    """Best-effort mapping onto the minimum exit-reason taxonomy this layer's spec asked for
    (ORIGINAL_TP/ORIGINAL_SL/BREAK_EVEN/TRAILING_STOP/ADAPTIVE_EXIT/STRUCTURE_INVALIDATION/
    PORTFOLIO_EXIT/MANUAL_EXIT/EMERGENCY_EXIT), derived from the REAL exit_reason taxonomy
    (TAKE_PROFIT/STOP_LOSS/PROFIT_EXIT/LOSS_EXIT/FLAT_EXIT, backend/brokers/mt5/persistence.py)
    plus the last confirmed management action before close. EMERGENCY_EXIT is not currently
    distinguishable from MANUAL_EXIT in this dataset (both map from VALIDATION_INCIDENT_CLOSE)
    -- documented limitation, never guessed."""
    sl_moved = final_sl is not None and original_sl is not None and abs(final_sl - original_sl) > 1e-9
    tp_moved = final_tp is not None and original_tp is not None and abs(final_tp - original_tp) > 1e-9
    if exit_reason == "TAKE_PROFIT":
        return "ORIGINAL_TP" if not tp_moved else "ADAPTIVE_EXIT"
    if exit_reason == "STOP_LOSS":
        if not sl_moved:
            return "ORIGINAL_SL"
        if last_action_type == "MOVE_SL_BREAKEVEN":
            return "BREAK_EVEN"
        if last_action_type in _TRAILING_ACTION_TYPES:
            return "TRAILING_STOP"
        if last_action_type == "TP_PROGRESS_STRUCTURE_STOP":
            return "STRUCTURE_INVALIDATION"
        return "TRAILING_STOP"
    if last_action_type == "THESIS_INVALIDATION_CLOSE":
        return "STRUCTURE_INVALIDATION"
    if last_action_type in {"EVENT_RISK_REDUCTION", "ECONOMIC_REDUCE_SIZE", "ECONOMIC_MANAGE_EXISTING_ONLY"}:
        return "PORTFOLIO_EXIT"
    if last_action_type in {"MFE_PROTECTION_CLOSE", "TIME_EXIT", "PARTIAL_PROFIT", "TP_PROGRESS_PARTIAL_PROTECT", "TP_PROGRESS_PROFIT_LOCK"}:
        return "ADAPTIVE_EXIT"
    if last_action_type == "VALIDATION_INCIDENT_CLOSE":
        return "MANUAL_EXIT"
    return "UNKNOWN"


def _position_records() -> list[dict[str, Any]]:
    """One record per closed, non-contaminated, baselined position -- the dataset every report
    function below operates on."""
    with SessionLocal() as db:
        states = (
            db.query(AdaptivePositionStateORM)
            .filter(AdaptivePositionStateORM.closed_detected_at.isnot(None), AdaptivePositionStateORM.contaminated.is_(False))
            .all()
        )
        baselines = {row.position_id: row for row in db.query(AdaptivePositionBaselineORM).all()}
        counterfactuals = {row.position_id: row for row in db.query(AdaptiveManagerCounterfactualORM).all()}
        position_ids = [s.position_id for s in states if s.position_id in baselines]
        deals_by_position: dict[str, list[AdaptiveTradeEventORM]] = defaultdict(list)
        if position_ids:
            for row in db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.event_type == "DEAL", AdaptiveTradeEventORM.position_id.in_(position_ids)).all():
                deals_by_position[row.position_id].append(row)
        confirmed_actions: dict[str, list[AdaptiveManagementActionORM]] = defaultdict(list)
        if position_ids:
            for row in db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.status == "submitted", AdaptiveManagementActionORM.position_id.in_(position_ids)).all():
                confirmed_actions[row.position_id].append(row)
        events_by_position: dict[str, list[AdaptiveManagementEventORM]] = defaultdict(list)
        if position_ids:
            for row in db.query(AdaptiveManagementEventORM).filter(AdaptiveManagementEventORM.position_id.in_(position_ids)).order_by(AdaptiveManagementEventORM.created_at.asc()).all():
                events_by_position[row.position_id].append(row)

    records: list[dict[str, Any]] = []
    for state in states:
        baseline = baselines.get(state.position_id)
        if baseline is None:
            continue
        deals = deals_by_position.get(state.position_id, [])
        exit_deal = max(deals, key=lambda d: d.utc_time or datetime.min.replace(tzinfo=timezone.utc), default=None)
        exit_reason = next((d.broker_exit_reason for d in deals if d.broker_exit_reason), None)
        realized_pnl = sum(float(d.realized_pnl or 0) + float(d.commission or 0) + float(d.swap or 0) + float(d.fee or 0) for d in deals) if deals else None
        initial_risk_money = baseline.initial_risk_money
        realized_r = round(realized_pnl / abs(initial_risk_money), 4) if (realized_pnl is not None and initial_risk_money) else None
        mfe_r = float(state.max_achieved_r) if state.max_achieved_r is not None else None
        mae_r = float(state.min_achieved_r) if state.min_achieved_r is not None else None
        exit_time = _aware(exit_deal.utc_time) if exit_deal else None
        opened_at = _aware(state.opened_at)
        holding_seconds = (exit_time - opened_at).total_seconds() if (exit_time and opened_at) else None

        actions = confirmed_actions.get(state.position_id, [])
        real_interventions = [a for a in actions if a.action_type not in _HOLD_ACTION_TYPES]
        sl_mods = [a for a in actions if a.requested_sl is not None]
        tp_mods = [a for a in actions if a.requested_tp is not None]
        last_action_type = max(actions, key=lambda a: a.created_at or datetime.min.replace(tzinfo=timezone.utc)).action_type if actions else None

        events = events_by_position.get(state.position_id, [])
        activation = AdaptiveManagerOutcomeResolver._find_be_activation(events)
        be_activated = activation is not None
        r_at_be, mfe_before_be, mfe_after_be, time_to_be_seconds = None, None, None, None
        if activation is not None and events:
            _pre_sl, activation_time = activation
            activation_time = _aware(activation_time)
            before = [e for e in events if _aware(e.created_at) and _aware(e.created_at) <= activation_time]
            after = [e for e in events if _aware(e.created_at) and _aware(e.created_at) > activation_time]
            if before:
                r_at_be = before[-1].current_r
                mfe_before_be = max((e.max_achieved_r for e in before if e.max_achieved_r is not None), default=None)
            if after:
                mfe_after_be = max((e.max_achieved_r for e in after if e.max_achieved_r is not None), default=None)
            if opened_at and activation_time:
                time_to_be_seconds = (activation_time - opened_at).total_seconds()

        exit_category = _derive_exit_category(exit_reason=exit_reason, final_sl=state.current_sl, original_sl=baseline.original_sl, final_tp=state.current_tp, original_tp=baseline.original_tp, last_action_type=last_action_type)

        cf = counterfactuals.get(state.position_id)

        records.append({
            "position_id": state.position_id,
            "symbol": state.symbol,
            "direction": state.direction,
            "strategy": baseline.original_strategy or "unknown",
            "market_regime": state.entry_regime or "unknown",
            "session": _session_label(opened_at),
            "confidence_band": baseline.confidence_band or "unknown",
            "original_confidence": baseline.original_confidence,
            "candidate_rank": baseline.candidate_rank,
            "original_sl": baseline.original_sl,
            "original_tp": baseline.original_tp,
            "initial_stop_distance": baseline.initial_stop_distance,
            "initial_reward_risk": baseline.initial_reward_risk,
            "initial_risk_money": initial_risk_money,
            "final_sl": state.current_sl,
            "final_tp": state.current_tp,
            "realized_pnl": realized_pnl,
            "realized_r": realized_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "capture_ratio": _capture_ratio(realized_r, mfe_r),
            "giveback_r": _giveback_r(realized_r, mfe_r),
            "holding_seconds": holding_seconds,
            "exit_reason": exit_reason or "unknown",
            "exit_category": exit_category,
            "num_interventions": len(real_interventions),
            "num_sl_mods": len(sl_mods),
            "num_tp_mods": len(tp_mods),
            "be_activated": be_activated,
            "r_at_be_activation": r_at_be,
            "mfe_before_be": mfe_before_be,
            "mfe_after_be": mfe_after_be,
            "time_to_be_seconds": time_to_be_seconds,
            "original_sltp_outcome": cf.original_sltp_outcome if cf else "PENDING",
            "original_sltp_r": cf.original_sltp_r if cf else None,
            "no_be_applicable": bool(cf.no_be_applicable) if cf else False,
            "no_be_outcome": cf.no_be_outcome if cf else "PENDING",
            "no_be_r": cf.no_be_r if cf else None,
            "post_exit_status": cf.post_exit_status if cf else "PENDING",
            "post_exit_reached_original_tp": cf.post_exit_reached_original_tp if cf else None,
            "post_exit_reached_plus_1r": cf.post_exit_reached_plus_1r if cf else None,
            "post_exit_reversed_strongly": cf.post_exit_reversed_strongly if cf else None,
            "post_exit_would_have_hit_original_sl": cf.post_exit_would_have_hit_original_sl if cf else None,
        })
    return records


# ---------------------------------------------------------------------- Part 14: core metrics ---
def core_metrics_report() -> dict[str, Any]:
    records = _position_records()
    resolved_r = [r["realized_r"] for r in records if r["realized_r"] is not None]
    wins = [r for r in resolved_r if r > 0]
    losses = [r for r in resolved_r if r < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    mfe_values = [r["mfe_r"] for r in records if r["mfe_r"] is not None]
    mae_values = [r["mae_r"] for r in records if r["mae_r"] is not None]
    capture_values = [r["capture_ratio"] for r in records if r["capture_ratio"] is not None]
    giveback_values = [r["giveback_r"] for r in records if r["giveback_r"] is not None]
    be_trades = [r for r in records if r["be_activated"]]
    trailing_trades = [r for r in records if r["exit_category"] == "TRAILING_STOP"]
    interventions = [r["num_interventions"] for r in records]
    losers_r = [r["realized_r"] for r in records if r["realized_r"] is not None and r["realized_r"] < 0]
    r_saved_on_losers = [(r - (-1.0)) for r in losers_r]  # positive = avoided loss vs a full -1R
    surrendered = [r["giveback_r"] for r in records if r["giveback_r"] is not None]

    baseline_r = [r["original_sltp_r"] for r in records if r["original_sltp_outcome"] not in {"PENDING", None}]
    managed_expectancy = round(statistics.fmean(resolved_r), 4) if resolved_r else None
    baseline_expectancy = round(statistics.fmean(baseline_r), 4) if baseline_r else None
    delta = round(managed_expectancy - baseline_expectancy, 4) if (managed_expectancy is not None and baseline_expectancy is not None) else None

    early_exit_candidates = [r for r in records if r["post_exit_status"] == "RESOLVED" and (r["post_exit_reached_original_tp"] or r["post_exit_reached_plus_1r"])]
    protection_candidates = [r for r in records if r["realized_r"] is not None and r["original_sltp_r"] is not None and r["original_sltp_outcome"] not in {"PENDING"} and r["realized_r"] > r["original_sltp_r"]]

    n = len(records)
    return {
        "total_managed_trades": n,
        "sample_label": sample_label(n),
        "realized_expectancy": managed_expectancy,
        "avg_r": managed_expectancy,
        "median_r": round(statistics.median(resolved_r), 4) if resolved_r else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "average_mfe_r": round(statistics.fmean(mfe_values), 4) if mfe_values else None,
        "average_mae_r": round(statistics.fmean(mae_values), 4) if mae_values else None,
        "average_capture_ratio": round(statistics.fmean(capture_values), 4) if capture_values else None,
        "average_giveback_r": round(statistics.fmean(giveback_values), 4) if giveback_values else None,
        "break_even_frequency": round(len(be_trades) / n, 4) if n else None,
        "trailing_frequency": round(len(trailing_trades) / n, 4) if n else None,
        "average_interventions_per_trade": round(statistics.fmean(interventions), 4) if interventions else None,
        "early_exit_indicator_rate": round(len(early_exit_candidates) / n, 4) if n else None,
        "protection_value_rate": round(len(protection_candidates) / n, 4) if n else None,
        "average_r_saved_on_losers": round(statistics.fmean(r_saved_on_losers), 4) if r_saved_on_losers else None,
        "average_r_surrendered_from_mfe": round(statistics.fmean(surrendered), 4) if surrendered else None,
        "original_sltp_baseline_expectancy": baseline_expectancy,
        "managed_expectancy": managed_expectancy,
        "manager_expectancy_delta": delta,
    }


# ------------------------------------------------------------------ Part 4: break-even analysis ---
def break_even_analysis_report() -> dict[str, Any]:
    records = _position_records()
    be_trades = [r for r in records if r["be_activated"]]
    r_at_be = [r["r_at_be_activation"] for r in be_trades if r["r_at_be_activation"] is not None]
    time_to_be = [r["time_to_be_seconds"] for r in be_trades if r["time_to_be_seconds"] is not None]
    mfe_after_be = [r["mfe_after_be"] for r in be_trades if r["mfe_after_be"] is not None]
    realized_r_be = [r["realized_r"] for r in be_trades if r["realized_r"] is not None]
    mfe_r_be = [r["mfe_r"] for r in be_trades if r["mfe_r"] is not None]
    near_breakeven_exit = [r for r in be_trades if r["realized_r"] is not None and -0.15 <= r["realized_r"] <= 0.15]
    would_have_reached_tp = [r for r in be_trades if r["no_be_applicable"] and r["no_be_outcome"] == "ORIGINAL_TP_FIRST"]
    stopped_at_be_then_continued = [r for r in be_trades if r["exit_category"] == "BREAK_EVEN" and (r["post_exit_reached_original_tp"] or r["post_exit_reached_plus_1r"])]
    resolved_no_be = [r for r in be_trades if r["no_be_applicable"] and r["no_be_outcome"] not in {"PENDING"}]

    n = len(be_trades)
    return {
        "trades_with_be_activated": n,
        "sample_label": sample_label(n),
        "avg_r_at_be_activation": round(statistics.fmean(r_at_be), 4) if r_at_be else None,
        "median_r_at_be_activation": round(statistics.median(r_at_be), 4) if r_at_be else None,
        "avg_time_to_be_seconds": round(statistics.fmean(time_to_be), 1) if time_to_be else None,
        "pct_exited_at_or_near_breakeven": round(len(near_breakeven_exit) / n, 4) if n else None,
        "avg_mfe_after_be": round(statistics.fmean(mfe_after_be), 4) if mfe_after_be else None,
        "pct_would_have_reached_original_tp": round(len(would_have_reached_tp) / len(resolved_no_be), 4) if resolved_no_be else None,
        "pct_stopped_at_be_before_continuing": round(len(stopped_at_be_then_continued) / n, 4) if n else None,
        "avg_realized_r_of_be_trades": round(statistics.fmean(realized_r_be), 4) if realized_r_be else None,
        "avg_mfe_of_be_trades": round(statistics.fmean(mfe_r_be), 4) if mfe_r_be else None,
    }


# --------------------------------------------------------------- Part 5: winner preservation ---
def winner_preservation_report() -> list[dict[str, Any]]:
    records = _position_records()
    results = []
    for threshold in WINNER_BUCKETS:
        bucket = [r for r in records if r["mfe_r"] is not None and r["mfe_r"] >= threshold]
        n = len(bucket)
        realized = [r["realized_r"] for r in bucket if r["realized_r"] is not None]
        mfe_values = [r["mfe_r"] for r in bucket]
        giveback = [r["giveback_r"] for r in bucket if r["giveback_r"] is not None]
        capture = [r["capture_ratio"] for r in bucket if r["capture_ratio"] is not None]
        negative = [r for r in bucket if r["realized_r"] is not None and r["realized_r"] < 0]
        near_be = [r for r in bucket if r["realized_r"] is not None and -0.15 <= r["realized_r"] <= 0.15]
        reached_tp = [r for r in bucket if r["exit_category"] == "ORIGINAL_TP"]
        adaptive_exit = [r for r in bucket if r["exit_category"] == "ADAPTIVE_EXIT"]
        trailing_exit = [r for r in bucket if r["exit_category"] == "TRAILING_STOP"]
        results.append({
            "bucket": f">= +{threshold}R",
            "count": n,
            "sample_label": sample_label(n),
            "avg_realized_r": round(statistics.fmean(realized), 4) if realized else None,
            "median_realized_r": round(statistics.median(realized), 4) if realized else None,
            "avg_mfe": round(statistics.fmean(mfe_values), 4) if mfe_values else None,
            "avg_giveback": round(statistics.fmean(giveback), 4) if giveback else None,
            "avg_capture_ratio": round(statistics.fmean(capture), 4) if capture else None,
            "pct_closed_negative": round(len(negative) / n, 4) if n else None,
            "pct_closed_near_breakeven": round(len(near_be) / n, 4) if n else None,
            "pct_reached_tp": round(len(reached_tp) / n, 4) if n else None,
            "pct_exited_by_adaptive_manager": round(len(adaptive_exit) / n, 4) if n else None,
            "pct_exited_by_trailing_stop": round(len(trailing_exit) / n, 4) if n else None,
        })
    return results


# ------------------------------------------------------------------ Part 6: loser protection ---
def loser_protection_report() -> dict[str, Any]:
    records = _position_records()
    losers = [r for r in records if r["realized_r"] is not None and r["realized_r"] < 0]
    mae_values = [r["mae_r"] for r in losers if r["mae_r"] is not None]
    realized_r = [r["realized_r"] for r in losers]
    reduced_loss = [r for r in losers if r["realized_r"] > -1.0]  # better than a full original -1R
    tightened_before_exit = [r for r in losers if r["num_sl_mods"] > 0]
    exited_before_original_sl = [r for r in losers if r["exit_category"] != "ORIGINAL_SL"]
    loss_avoided = [(r["realized_r"] - (-1.0)) for r in losers]  # positive = avoided loss vs a full -1R
    intervention_made_worse = [r for r in losers if r["num_interventions"] > 0 and r["realized_r"] < -1.0]

    n = len(losers)
    return {
        "losing_trades": n,
        "sample_label": sample_label(n),
        "avg_mae_r": round(statistics.fmean(mae_values), 4) if mae_values else None,
        "avg_final_realized_r": round(statistics.fmean(realized_r), 4) if realized_r else None,
        "pct_manager_reduced_loss_vs_original_risk": round(len(reduced_loss) / n, 4) if n else None,
        "pct_sl_tightened_before_exit": round(len(tightened_before_exit) / n, 4) if n else None,
        "pct_exited_before_original_sl": round(len(exited_before_original_sl) / n, 4) if n else None,
        "avg_loss_avoided_vs_initial_risk": round(statistics.fmean(loss_avoided), 4) if loss_avoided else None,
        "trades_where_intervention_made_loss_worse": len(intervention_made_worse),
    }


# --------------------------------------------------------------------- Part 7: manager value-add ---
def manager_value_add_report() -> dict[str, Any]:
    records = _position_records()
    resolved_baseline = [r for r in records if r["original_sltp_outcome"] not in {"PENDING", None} and r["realized_r"] is not None]

    protection_value = [r for r in resolved_baseline if r["realized_r"] > r["original_sltp_r"]]
    potential_value_destruction = [r for r in resolved_baseline if r["realized_r"] < r["original_sltp_r"]]

    post_exit_opportunities = [
        {
            "position_id": r["position_id"], "symbol": r["symbol"], "exit_category": r["exit_category"],
            "realized_r": r["realized_r"], "label": "POST_EXIT_OPPORTUNITY",
        }
        for r in records
        if r["post_exit_status"] == "RESOLVED" and (r["post_exit_reached_original_tp"] or r["post_exit_reached_plus_1r"])
    ]
    potential_early_exits = [
        {
            "position_id": r["position_id"], "symbol": r["symbol"], "exit_category": r["exit_category"],
            "realized_r": r["realized_r"], "no_be_outcome": r["no_be_outcome"], "label": "POTENTIAL_EARLY_EXIT",
        }
        for r in records
        if r["no_be_applicable"] and r["no_be_outcome"] == "ORIGINAL_TP_FIRST"
    ]

    n = len(resolved_baseline)
    return {
        "sample_size": n,
        "sample_label": sample_label(n),
        "protection_value_count": len(protection_value),
        "protection_value_rate": round(len(protection_value) / n, 4) if n else None,
        "protection_value_examples": [{"position_id": r["position_id"], "symbol": r["symbol"], "realized_r": r["realized_r"], "original_sltp_r": r["original_sltp_r"]} for r in protection_value[:20]],
        "potential_value_destruction_count": len(potential_value_destruction),
        "potential_value_destruction_rate": round(len(potential_value_destruction) / n, 4) if n else None,
        # Deliberately not labeled a mistake -- future price movement does not prove the
        # original management decision was wrong (Part 7).
        "post_exit_opportunity_flags": post_exit_opportunities[:50],
        "potential_early_exit_flags": potential_early_exits[:50],
    }


# --------------------------------------------------------------------- Part 9: exit reason ---
def exit_reason_analytics_report() -> list[dict[str, Any]]:
    records = _position_records()
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_category[r["exit_category"]].append(r)

    results = []
    for category, rows in sorted(by_category.items()):
        n = len(rows)
        realized = [r["realized_r"] for r in rows if r["realized_r"] is not None]
        wins = [r for r in realized if r > 0]
        mfe_values = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
        mae_values = [r["mae_r"] for r in rows if r["mae_r"] is not None]
        capture = [r["capture_ratio"] for r in rows if r["capture_ratio"] is not None]
        giveback = [r["giveback_r"] for r in rows if r["giveback_r"] is not None]
        holding = [r["holding_seconds"] for r in rows if r["holding_seconds"] is not None]
        results.append({
            "exit_category": category,
            "count": n,
            "sample_label": sample_label(n),
            "win_rate": round(len(wins) / len(realized), 4) if realized else None,
            "avg_r": round(statistics.fmean(realized), 4) if realized else None,
            "median_r": round(statistics.median(realized), 4) if realized else None,
            "avg_mfe": round(statistics.fmean(mfe_values), 4) if mfe_values else None,
            "avg_mae": round(statistics.fmean(mae_values), 4) if mae_values else None,
            "avg_capture_ratio": round(statistics.fmean(capture), 4) if capture else None,
            "avg_giveback": round(statistics.fmean(giveback), 4) if giveback else None,
            "avg_holding_seconds": round(statistics.fmean(holding), 1) if holding else None,
        })
    return results


# ----------------------------------------------------------------- Part 10: intervention frequency ---
def intervention_frequency_report() -> dict[str, Any]:
    records = _position_records()
    interventions = [r["num_interventions"] for r in records]
    sl_mods = [r["num_sl_mods"] for r in records]
    tp_mods = [r["num_tp_mods"] for r in records]
    holding_hours = [(r["holding_seconds"] / 3600.0) for r in records if r["holding_seconds"]]
    total_mods = sum(sl_mods) + sum(tp_mods)
    total_hours = sum(holding_hours)

    buckets_def = [("0", lambda n: n == 0), ("1", lambda n: n == 1), ("2-3", lambda n: 2 <= n <= 3), ("4-5", lambda n: 4 <= n <= 5), ("6+", lambda n: n >= 6)]
    by_bucket = []
    for label, predicate in buckets_def:
        rows = [r for r in records if predicate(r["num_interventions"])]
        n = len(rows)
        realized = [r["realized_r"] for r in rows if r["realized_r"] is not None]
        by_bucket.append({
            "interventions": label,
            "count": n,
            "sample_label": sample_label(n),
            "avg_r": round(statistics.fmean(realized), 4) if realized else None,
        })

    n = len(records)
    return {
        "total_managed_trades": n,
        "sample_label": sample_label(n),
        "average_interventions_per_trade": round(statistics.fmean(interventions), 4) if interventions else None,
        "median_interventions_per_trade": round(statistics.median(interventions), 4) if interventions else None,
        "modifications_per_hour": round(total_mods / total_hours, 4) if total_hours > 0 else None,
        "avg_sl_moves_per_trade": round(statistics.fmean(sl_mods), 4) if sl_mods else None,
        "avg_tp_moves_per_trade": round(statistics.fmean(tp_mods), 4) if tp_mods else None,
        "by_intervention_count": by_bucket,
    }


# ------------------------------------------------------------- Part 11: confidence-band management ---
def confidence_band_management_report() -> dict[str, Any]:
    records = _position_records()
    by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        band = r["confidence_band"] if r["confidence_band"] in {"70-74", "75-79", "80-84", "85-89", "90+"} else None
        if band:
            by_band[band].append(r)

    results = {}
    for band in CONFIDENCE_BANDS:
        rows = by_band.get(band, [])
        n = len(rows)
        realized = [r["realized_r"] for r in rows if r["realized_r"] is not None]
        be_rate = len([r for r in rows if r["be_activated"]]) / n if n else None
        capture = [r["capture_ratio"] for r in rows if r["capture_ratio"] is not None]
        results[band] = {
            "count": n,
            "sample_label": sample_label(n),
            "avg_r": round(statistics.fmean(realized), 4) if realized else None,
            "break_even_rate": round(be_rate, 4) if be_rate is not None else None,
            "avg_capture_ratio": round(statistics.fmean(capture), 4) if capture else None,
            "avg_interventions": round(statistics.fmean([r["num_interventions"] for r in rows]), 4) if rows else None,
        }
    return results


# --------------------------------------------------------- Part 12: symbol / strategy / regime ---
def symbol_strategy_regime_report() -> dict[str, list[dict[str, Any]]]:
    records = _position_records()

    def _group_by(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            groups[str(r.get(key) or "unknown")].append(r)
        rows = []
        for name, group in sorted(groups.items()):
            n = len(group)
            realized = [g["realized_r"] for g in group if g["realized_r"] is not None]
            be_rate = len([g for g in group if g["be_activated"]]) / n if n else None
            capture = [g["capture_ratio"] for g in group if g["capture_ratio"] is not None]
            rows.append({
                key: name,
                "count": n,
                "sample_label": sample_label(n),
                "avg_r": round(statistics.fmean(realized), 4) if realized else None,
                "break_even_rate": round(be_rate, 4) if be_rate is not None else None,
                "avg_capture_ratio": round(statistics.fmean(capture), 4) if capture else None,
            })
        return rows

    return {
        "symbol": _group_by("symbol"),
        "strategy": _group_by("strategy"),
        "market_regime": _group_by("market_regime"),
        "session": _group_by("session"),
        "direction": _group_by("direction"),
    }


def recent_management_events(limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(AdaptiveManagementEventORM).order_by(AdaptiveManagementEventORM.created_at.desc()).limit(limit).all()
        return [{column.name: getattr(row, column.name) for column in AdaptiveManagementEventORM.__table__.columns} for row in rows]

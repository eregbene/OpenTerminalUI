"""Phase 11 (Forex/MT5 roadmap): profit-retention policy comparison across the +0.25R-+2R
milestones.

Deliberately NOT a new counterfactual engine -- backend/adaptive_management/service.py already
has one, built and running long before this phase: `replay()` simulates every named management
policy (default_policies(): static_baseline_v1, partial-profit, breakeven, ATR trailing, MFE
retracement/structure-stop, momentum-invalidation, time-exit, TP-progress protection zones,
ATR/structure-stop-width variants -- i.e. exactly the HOLD/partial/BE/trail/MFE-protection/
thesis-invalidation/reduced-risk-SL families the roadmap names) against every real closed trade's
reconstructed price path, and persists one CounterfactualOutcomeORM row per (trade, policy) with
an ALREADY R-normalized `hypothetical_r` and `mfe_capture`. `evaluate_policies()` /
`run_walk_forward()` / `_champion_challenger()` already aggregate and champion/challenge those
rows (always with `promotion_allowed=False, manual_approval_required=True` -- policies are never
auto-promoted).

What was genuinely missing, and is the entire scope of this module: a MILESTONE-SEGMENTED view.
The existing aggregation pools every trade together regardless of how far into profit it got --
this instead answers "among trades that reached AT LEAST +0.5R (or +1R, +1.5R, +2R) of real,
achieved MFE (TradePathSnapshotORM.max_achieved_r, the trade's ACTUAL reconstructed price path,
not a hypothetical one), how does each policy's average hypothetical_r and mfe_capture compare to
the static baseline ON THAT SAME TRADE SUBSET" -- i.e. expected additional R and capture ratio,
specifically for trades that had real profit-retention decisions to make.

Read-only, analytics-only: this module writes nothing and promotes nothing. Policy promotion
remains exactly where it already was -- _champion_challenger's persisted recommendation, gated on
manual approval.
"""
from __future__ import annotations

from typing import Any

MILESTONES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
MIN_SAMPLE = 10
BASELINE_POLICY_ID = "static_baseline_v1"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def milestone_policy_comparison(*, milestones: tuple[float, ...] = MILESTONES, min_sample: int = MIN_SAMPLE) -> dict[str, Any]:
    """Real query against the existing counterfactual-replay corpus. For each milestone, restricts
    to trades whose REAL reconstructed path reached at least that many R of MFE, then compares
    every policy's average hypothetical_r/mfe_capture against the baseline on that exact subset."""
    from backend.adaptive_management.orm import AdaptiveTradeEventORM, CounterfactualOutcomeORM, TradePathSnapshotORM
    from backend.adaptive_management.service import _contaminated_position_ids
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        contaminated = _contaminated_position_ids(db)
        rows = (
            db.query(CounterfactualOutcomeORM, TradePathSnapshotORM.max_achieved_r)
            .join(TradePathSnapshotORM, TradePathSnapshotORM.trade_id == CounterfactualOutcomeORM.trade_id)
            .filter(CounterfactualOutcomeORM.trade_id.notin_(contaminated), CounterfactualOutcomeORM.applicable.is_(True))
            .all()
        )

    milestone_results: dict[str, Any] = {}
    for milestone in milestones:
        subset = [(outcome, mfe_r) for outcome, mfe_r in rows if mfe_r is not None and mfe_r >= milestone]
        trade_ids = {outcome.trade_id for outcome, _ in subset}
        by_policy: dict[str, list[Any]] = {}
        for outcome, _mfe_r in subset:
            by_policy.setdefault(outcome.policy_id, []).append(outcome)

        baseline_rows = by_policy.get(BASELINE_POLICY_ID, [])
        baseline_avg_r = _avg([o.hypothetical_r for o in baseline_rows])

        policies: dict[str, Any] = {}
        for policy_id, policy_rows in by_policy.items():
            sample_size = len(policy_rows)
            avg_r = _avg([o.hypothetical_r for o in policy_rows])
            avg_capture = _avg([o.mfe_capture for o in policy_rows])
            expected_additional_r = (avg_r - baseline_avg_r) if (avg_r is not None and baseline_avg_r is not None and policy_id != BASELINE_POLICY_ID) else (0.0 if policy_id == BASELINE_POLICY_ID else None)
            policies[policy_id] = {
                "sample_size": sample_size,
                "avg_hypothetical_r": round(avg_r, 4) if avg_r is not None else None,
                "avg_mfe_capture": round(avg_capture, 4) if avg_capture is not None else None,
                "expected_additional_r_vs_baseline": round(expected_additional_r, 4) if expected_additional_r is not None else None,
                "insufficient_sample": sample_size < min_sample,
            }

        milestone_results[f"mfe_at_least_{milestone}R"] = {
            "trades_reaching_milestone": len(trade_ids),
            "baseline_policy_id": BASELINE_POLICY_ID,
            "baseline_sample_size": len(baseline_rows),
            "policies": policies,
        }

    return {"milestones": milestone_results}


def best_policy_per_milestone(comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convenience view: for each milestone, which non-baseline policy shows the highest expected
    additional R with enough sample -- informational ranking only, never an auto-promotion
    (promotion stays exclusively _champion_challenger's, manual-approval-gated, path)."""
    comparison = comparison or milestone_policy_comparison()
    best: dict[str, Any] = {}
    for milestone_key, data in comparison["milestones"].items():
        candidates = [
            (policy_id, stats["expected_additional_r_vs_baseline"])
            for policy_id, stats in data["policies"].items()
            if policy_id != BASELINE_POLICY_ID and not stats["insufficient_sample"] and stats["expected_additional_r_vs_baseline"] is not None
        ]
        if not candidates:
            best[milestone_key] = {"best_policy_id": None, "expected_additional_r_vs_baseline": None, "reason": "INSUFFICIENT_SAMPLE"}
            continue
        best_policy_id, best_r = max(candidates, key=lambda item: item[1])
        best[milestone_key] = {"best_policy_id": best_policy_id, "expected_additional_r_vs_baseline": best_r}
    return best

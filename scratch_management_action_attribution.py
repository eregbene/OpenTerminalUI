"""Bensim -- Adaptive Manager V3 continuation, Part 8: management action attribution.

Per strategy, per action_type, real counts and R impact -- reusing EXISTING infrastructure
rather than duplicating it:
  - adaptive_management_actions (AdaptiveManagementActionORM): one row per candidate action the
    manager considered each evaluation tick; `selected=True` rows are the ones that actually
    fired. `evidence` JSON carries the R-at-decision-time for most action types (key "r").
  - adaptive_manager_counterfactuals (AdaptiveManagerCounterfactualORM): the ALREADY-EXISTING
    post-hoc "what would have happened without this action" tracker -- `post_exit_additional_r_
    available` (R left on the table after an exit-type action), `post_exit_would_have_hit_
    original_sl` (did continuing to hold eventually get stopped out anyway), `no_be_r` (outcome
    had breakeven never been applied). One row per position_id, not per individual action --
    so for a position with multiple fired actions, the counterfactual is attached to the
    position's LAST fired action of a matching category, which is an approximation, documented
    inline, not silently assumed precise.

STATIC_R/MANAGED_R/MANAGER_DELTA_R at the strategy level is already computed by
scratch_unified_strategy_dashboard.py -- not duplicated here.

Cheap, pure read -- safe to re-run any time.
"""
import statistics as st
from collections import defaultdict

from backend.adaptive_management.orm import (
    AdaptiveManagementActionORM,
    AdaptiveManagerCounterfactualORM,
    AdaptivePositionStateORM,
)
from backend.shared.db import SessionLocal

TIER = {
    "mtfai1": "A", "trend_pullback": "A", "mean_reversion": "A",
    "vwap_reversion": "B", "liquidity_sweep_reversal": "B", "smc_continuation": "B",
    "support_resistance_bounce": "C", "breakout": "C", "ema_trend": "C", "session_breakout": "C",
}

# Action types that are FULL exits -- counterfactual is "R left on the table by exiting here"
# (post_exit_additional_r_available), and a premature exit is one where price kept running in
# our favor afterward without ever coming back to the original stop.
FULL_EXIT_ACTIONS = {"MFE_PROTECTION_CLOSE", "THESIS_INVALIDATION_CLOSE", "STRUCTURAL_REACCEPTANCE_CLOSE", "TIME_EXIT", "VALIDATION_INCIDENT_CLOSE"}
# Risk-management actions that MODIFY the trade but don't close it -- counterfactual is "no_be_r"
# (what the original SL/TP outcome would have been without breakeven/reduced-risk protection).
RISK_ADJUST_ACTIONS = {"MOVE_SL_BREAKEVEN", "MOVE_SL_TO_REDUCED_RISK"}
# No dedicated counterfactual column exists for these -- reported as FIRES/AVG_R_AT_ACTION only.
NO_COUNTERFACTUAL_ACTIONS = {"PARTIAL_PROFIT", "TRAIL_STOP", "EXTEND_TP", "REDUCE_TP", "ACCOUNT_PROFIT_LOCK", "EVENT_RISK_REDUCTION", "TP_PROGRESS_PARTIAL_PROTECT", "TP_PROGRESS_PROFIT_LOCK", "TP_PROGRESS_STRUCTURE_STOP"}

PREMATURE_EXIT_R_THRESHOLD = 0.3  # R left on the table beyond which an exit is flagged premature


def main():
    with SessionLocal() as db:
        position_strategy = {p.position_id: p.strategy_id for p in db.query(AdaptivePositionStateORM.position_id, AdaptivePositionStateORM.strategy_id).all()}
        counterfactuals = {c.position_id: c for c in db.query(AdaptiveManagerCounterfactualORM).all()}

        actions = db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.selected.is_(True)).all()

        by_strategy_action: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for a in actions:
            strategy_id = position_strategy.get(a.position_id)
            if not strategy_id:
                continue
            by_strategy_action[strategy_id][a.action_type].append(a)

        for strategy_id, tier in TIER.items():
            action_map = by_strategy_action.get(strategy_id, {})
            if not action_map:
                print(f"\n{'='*100}\n{strategy_id}  [Tier {tier}]\n{'='*100}\n  No fired management actions yet.")
                continue
            print(f"\n{'='*100}\n{strategy_id}  [Tier {tier}]\n{'='*100}")
            print(f"  {'ACTION':<28}{'FIRES':>7}{'AVG_R_AT_ACTION':>18}{'NET_R_SAVED_OR_LOST':>22}{'PREMATURE_EXITS':>17}")
            for action_type, rows in sorted(action_map.items(), key=lambda kv: -len(kv[1])):
                fires = len(rows)
                rs = [r.evidence.get("r") for r in rows if isinstance(r.evidence, dict) and r.evidence.get("r") is not None]
                avg_r = st.fmean(rs) if rs else None

                net_saved_or_lost = None
                premature = 0
                if action_type in FULL_EXIT_ACTIONS:
                    deltas = []
                    for r in rows:
                        cf = counterfactuals.get(r.position_id)
                        if cf is None or cf.post_exit_additional_r_available is None:
                            continue
                        left_on_table = cf.post_exit_additional_r_available
                        if cf.post_exit_would_have_hit_original_sl:
                            deltas.append(left_on_table)  # exiting protected us -- R "saved"
                        else:
                            deltas.append(-left_on_table)  # exiting cost us -- R "lost"
                            if left_on_table > PREMATURE_EXIT_R_THRESHOLD:
                                premature += 1
                    if deltas:
                        net_saved_or_lost = sum(deltas)
                elif action_type in RISK_ADJUST_ACTIONS:
                    deltas = []
                    for r in rows:
                        cf = counterfactuals.get(r.position_id)
                        if cf is None or cf.no_be_r is None or cf.original_sltp_r is None:
                            continue
                        deltas.append(cf.original_sltp_r - cf.no_be_r)
                    if deltas:
                        net_saved_or_lost = sum(deltas)

                avg_r_s = f"{avg_r:+.3f}" if avg_r is not None else "n/a"
                net_s = f"{net_saved_or_lost:+.3f}" if net_saved_or_lost is not None else ("n/a (no CF)" if action_type in NO_COUNTERFACTUAL_ACTIONS else "n/a (no matched CF rows)")
                print(f"  {action_type:<28}{fires:>7}{avg_r_s:>18}{net_s:>22}{premature:>17}")


if __name__ == "__main__":
    main()

from __future__ import annotations

from backend.adaptive_management.policy_library import ARCHETYPE_POLICIES, select_policy_for_strategy


def test_seven_archetype_policies_defined_with_distinct_ids():
    assert len(ARCHETYPE_POLICIES) == 7
    ids = {policy["policy_id"] for policy in ARCHETYPE_POLICIES}
    assert len(ids) == 7


def test_every_archetype_bundles_all_required_management_dimensions():
    required_keys = {"breakeven_trigger_r", "partial_stages", "trailing", "tp_reward_multiple", "max_holding_candles"}
    for policy in ARCHETYPE_POLICIES:
        assert required_keys <= set(policy["parameters"]), f"{policy['policy_id']} missing a required management dimension"
        assert policy["eligible_strategies"], f"{policy['policy_id']} must declare which strategies it applies to"


def test_trend_manager_holds_longer_and_trails_wider_than_scalping_manager():
    trend = next(p for p in ARCHETYPE_POLICIES if p["policy_id"] == "trend_manager_v1")
    scalping = next(p for p in ARCHETYPE_POLICIES if p["policy_id"] == "scalping_manager_v1")
    assert trend["parameters"]["max_holding_candles"] > scalping["parameters"]["max_holding_candles"]
    assert trend["parameters"]["trailing"]["trail_r"] > scalping["parameters"]["trailing"]["trail_r"]
    assert trend["parameters"]["tp_reward_multiple"] > scalping["parameters"]["tp_reward_multiple"]


def test_mean_reversion_manager_has_no_trailing_fixed_target_instead():
    mean_reversion = next(p for p in ARCHETYPE_POLICIES if p["policy_id"] == "mean_reversion_manager_v1")
    assert mean_reversion["parameters"]["trailing"] is None


def test_select_policy_for_strategy_matches_declared_strategy():
    assert select_policy_for_strategy("SMC") == "smc_manager_v1"
    assert select_policy_for_strategy("ict") == "ict_manager_v1"  # case-insensitive
    assert select_policy_for_strategy("SWING") == "swing_manager_v1"


def test_select_policy_for_strategy_falls_back_to_smc_for_unknown_strategy():
    assert select_policy_for_strategy("SOME_UNMAPPED_STRATEGY") == "smc_manager_v1"
    assert select_policy_for_strategy(None) == "smc_manager_v1"


def test_select_policy_for_strategy_respects_available_policy_restriction():
    # Even though ICT would normally match ict_manager_v1, if that policy isn't actually
    # available (e.g. not yet upserted into the DB), selection must fall back rather than
    # return a policy_id nothing can look up.
    result = select_policy_for_strategy("ICT", available_policy_ids={"smc_manager_v1", "trend_manager_v1"})
    assert result == "smc_manager_v1"

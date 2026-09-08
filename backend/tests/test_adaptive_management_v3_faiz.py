from decimal import Decimal

from backend.adaptive_management.v3_faiz import (
    V3AdaptiveRoutingBucket,
    V3BrokerRules,
    V3NextSessionProfile,
    V3AdaptiveBucketStats,
    V3AdaptiveState,
    V3AdaptiveDecision,
    apply_v3_hold_guard_research,
    allow_v3_demo_entry_from_bucket,
    allow_v3_demo_entry_from_profile,
    load_v3_next_session_profile,
    select_v3_demo_management_action,
    v3_broker_safe_partial_volume,
    v3_broker_safe_sl,
)


def test_v3_fixed_r_moves_to_breakeven_at_1r():
    decision = select_v3_demo_management_action(
        V3AdaptiveState(
            strategy_id="bsi_v3_4h_order_block",
            management_model="MENTOR_FIXED_1R_BE_2R_TARGET",
            entry=100.0,
            stop=99.0,
            target=102.0,
            current_price=101.0,
            direction="LONG",
        )
    )

    assert decision.action == "MOVE_SL_BREAKEVEN"
    assert decision.requested_sl == 100.0
    assert decision.reason == "v3_fixed_r_be_at_1r"


def test_v3_abc_takes_partial_at_2r_or_poi():
    decision = select_v3_demo_management_action(
        V3AdaptiveState(
            strategy_id="bsi_v3_abc",
            management_model="MENTOR_PARTIAL_AT_POI_OR_2R",
            entry=100.0,
            stop=99.0,
            target=103.0,
            current_price=102.0,
            direction="LONG",
        )
    )

    assert decision.action == "PARTIAL_CLOSE"
    assert decision.partial_fraction == 0.5
    assert decision.reason == "v3_partial_at_2r_or_poi"


def test_v3_range_models_partial_at_half_range():
    decision = select_v3_demo_management_action(
        V3AdaptiveState(
            strategy_id="bsi_v3_enigma_range",
            management_model="MENTOR_ENIGMA_PARTIAL_BE_0_5_TARGET_0_79_OR_1_0",
            entry=100.0,
            stop=98.0,
            target=104.0,
            current_price=101.0,
            direction="LONG",
        )
    )

    assert decision.action == "PARTIAL_CLOSE"
    assert decision.partial_fraction == 0.4
    assert decision.reason == "v3_range_partial_at_half_range"


def test_v3_unknown_management_model_holds():
    decision = select_v3_demo_management_action(
        V3AdaptiveState(
            strategy_id="bsi_v3_test",
            management_model="UNKNOWN",
            entry=100.0,
            stop=99.0,
            target=102.0,
            current_price=101.5,
            direction="LONG",
        )
    )

    assert decision.action == "HOLD"


def test_v3_research_hold_guard_blocks_arbitrary_early_full_close():
    guarded = apply_v3_hold_guard_research(
        V3AdaptiveState(
            strategy_id="bsi_v3_spectre",
            management_model="MENTOR_FINAL_TARGET_LIQUIDITY",
            entry=100.0,
            stop=99.0,
            target=103.0,
            current_price=100.4,
            direction="LONG",
        ),
        V3AdaptiveDecision("CLOSE", "generic_early_profit_take"),
    )

    assert guarded.action == "HOLD"
    assert guarded.reason == "v3_research_hold_guard_blocked_arbitrary_early_close"


def test_v3_research_hold_guard_allows_mentor_invalidation_before_08r():
    guarded = apply_v3_hold_guard_research(
        V3AdaptiveState(
            strategy_id="bsi_v3_spectre",
            management_model="MENTOR_FINAL_TARGET_LIQUIDITY",
            entry=100.0,
            stop=99.0,
            target=103.0,
            current_price=100.2,
            direction="LONG",
        ),
        V3AdaptiveDecision("CLOSE", "mentor_invalidation"),
    )

    assert guarded.action == "CLOSE"
    assert guarded.reason == "mentor_invalidation"


def test_v3_adaptive_bucket_gate_allows_positive_evidence():
    decision = allow_v3_demo_entry_from_bucket(
        V3AdaptiveBucketStats(
            trades=28,
            win_rate=57.14,
            expectancy=0.08,
            profit_factor=1.24,
            max_losing_streak=4,
        )
    )

    assert decision.allow_entry is True
    assert decision.reason == "v3_bucket_adaptive_evidence_pass"


def test_v3_adaptive_bucket_gate_blocks_negative_or_unstable_evidence():
    low_expectancy = allow_v3_demo_entry_from_bucket(
        V3AdaptiveBucketStats(
            trades=28,
            win_rate=48.0,
            expectancy=-0.03,
            profit_factor=0.92,
            max_losing_streak=5,
        )
    )
    high_losing_streak = allow_v3_demo_entry_from_bucket(
        V3AdaptiveBucketStats(
            trades=28,
            win_rate=58.0,
            expectancy=0.09,
            profit_factor=1.3,
            max_losing_streak=12,
        )
    )
    small_sample = allow_v3_demo_entry_from_bucket(
        V3AdaptiveBucketStats(
            trades=4,
            win_rate=75.0,
            expectancy=0.6,
            profit_factor=5.0,
            max_losing_streak=1,
        )
    )

    assert low_expectancy.allow_entry is False
    assert low_expectancy.reason == "v3_bucket_expectancy_below_threshold"
    assert high_losing_streak.allow_entry is False
    assert high_losing_streak.reason == "v3_bucket_losing_streak_too_high"
    assert small_sample.allow_entry is False
    assert small_sample.reason == "v3_bucket_sample_too_small"


def test_v3_broker_safe_sl_never_widens_risk_and_rounds_price():
    rules = V3BrokerRules(digits=3)

    assert v3_broker_safe_sl(direction="LONG", current_sl=99.0, requested_sl=100.0004, entry=100.0, rules=rules) == 100.0
    assert v3_broker_safe_sl(direction="LONG", current_sl=99.5, requested_sl=99.0, entry=100.0, rules=rules) is None
    assert v3_broker_safe_sl(direction="SHORT", current_sl=101.0, requested_sl=100.0, entry=100.0, rules=rules) == 100.0
    assert v3_broker_safe_sl(direction="SHORT", current_sl=100.5, requested_sl=101.0, entry=100.0, rules=rules) is None


def test_v3_broker_safe_partial_volume_floors_to_step_and_minimum():
    rules = V3BrokerRules(volume_min=Decimal("0.10"), volume_step=Decimal("0.10"))

    assert str(v3_broker_safe_partial_volume(current_volume=1.00, fraction=0.33, rules=rules)) == "0.30"
    assert str(v3_broker_safe_partial_volume(current_volume=0.15, fraction=0.5, rules=rules)) == "0"
    assert str(v3_broker_safe_partial_volume(current_volume=0.15, fraction=0.8, rules=rules)) == "0.15"


def test_v3_next_session_profile_loader_and_allowed_bucket(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(
        """
        {
          "profile_id": "BSI_V3_NEXT_SESSION_PROFILE_2026-09-08",
          "next_trading_day_utc_date": "2026-09-08",
          "methodology": "BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
          "as_of_utc_date": "2026-09-07",
          "risk_mode": "NORMAL",
          "allowed_buckets": [
            {
              "strategy_id": "bsi_v3_reactionary_block",
              "symbol": "EURUSD",
              "score": 3.0,
              "windows": ["year_to_date", "month_to_date"]
            }
          ],
          "risk_rules": {"generic_detector_routing_count": 0}
        }
        """,
        encoding="utf-8",
    )

    profile = load_v3_next_session_profile(path)
    decision = allow_v3_demo_entry_from_profile(
        profile,
        strategy_id="bsi_v3_reactionary_block",
        symbol="eurusd",
    )

    assert profile is not None
    assert profile.point_in_time_safe is True
    assert profile.allowed_buckets[0].symbol == "EURUSD"
    assert decision.allow_entry is True
    assert decision.reason == "v3_profile_bucket_allowed_next_session"
    assert decision.profile_id == "BSI_V3_NEXT_SESSION_PROFILE_2026-09-08"


def test_v3_next_session_profile_blocks_missing_but_allows_neutral_unlisted_bucket():
    missing = allow_v3_demo_entry_from_profile(None, strategy_id="bsi_v3_abc", symbol="EURUSD")
    profile = V3NextSessionProfile(
        profile_id="P1",
        next_trading_day_utc_date="2026-09-08",
        risk_mode="NORMAL",
        allowed_buckets=(V3AdaptiveRoutingBucket("bsi_v3_abc", "GBPUSD", 3.0, ("year_to_date",)),),
        methodology="BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
        as_of_utc_date="2026-09-07",
        point_in_time_safe=True,
    )
    unlisted = allow_v3_demo_entry_from_profile(profile, strategy_id="bsi_v3_abc", symbol="EURUSD")

    assert missing.allow_entry is False
    assert missing.reason == "v3_next_session_profile_missing"
    assert unlisted.allow_entry is True
    assert unlisted.reason == "v3_profile_bucket_neutral_rank_not_hard_blocked"


def test_v3_next_session_profile_blocks_paused_or_generic_routes():
    paused = V3NextSessionProfile(
        profile_id="P1",
        next_trading_day_utc_date="2026-09-08",
        risk_mode="PAUSE_NEW_V3_ENTRIES",
        allowed_buckets=(V3AdaptiveRoutingBucket("bsi_v3_abc", "EURUSD", 3.0, ("year_to_date",)),),
        methodology="BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
        as_of_utc_date="2026-09-07",
        point_in_time_safe=True,
    )
    generic = V3NextSessionProfile(
        profile_id="P2",
        next_trading_day_utc_date="2026-09-08",
        risk_mode="NORMAL",
        allowed_buckets=(V3AdaptiveRoutingBucket("bsi_v3_abc", "EURUSD", 3.0, ("year_to_date",)),),
        generic_detector_routing_count=1,
        methodology="BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
        as_of_utc_date="2026-09-07",
        point_in_time_safe=True,
    )

    assert allow_v3_demo_entry_from_profile(paused, strategy_id="bsi_v3_abc", symbol="EURUSD").reason == "v3_profile_risk_mode_paused"
    assert allow_v3_demo_entry_from_profile(generic, strategy_id="bsi_v3_abc", symbol="EURUSD").reason == "v3_profile_contains_generic_detector_routes"


def test_v3_next_session_profile_fails_closed_when_not_point_in_time_safe():
    leaky = V3NextSessionProfile(
        profile_id="P3",
        next_trading_day_utc_date="2026-09-08",
        risk_mode="NORMAL",
        allowed_buckets=(V3AdaptiveRoutingBucket("bsi_v3_abc", "EURUSD", 3.0, ("full_period",)),),
        methodology="BSI_V3_FULL_PERIOD_RESEARCH",
        as_of_utc_date="2026-09-08",
        point_in_time_safe=False,
    )

    decision = allow_v3_demo_entry_from_profile(leaky, strategy_id="bsi_v3_abc", symbol="EURUSD")

    assert decision.allow_entry is False
    assert decision.reason == "v3_profile_not_point_in_time_safe"


def test_v3_next_session_profile_blocks_reliable_hard_block_bucket():
    profile = V3NextSessionProfile(
        profile_id="P4",
        next_trading_day_utc_date="2026-09-08",
        risk_mode="NORMAL",
        allowed_buckets=(),
        blocked_buckets=(V3AdaptiveRoutingBucket("bsi_v3_spectre", "EURUSD", -2.0, ("month_to_date",), "BLOCK", 0.9),),
        methodology="BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
        as_of_utc_date="2026-09-07",
        point_in_time_safe=True,
    )

    decision = allow_v3_demo_entry_from_profile(profile, strategy_id="bsi_v3_spectre", symbol="EURUSD")

    assert decision.allow_entry is False
    assert decision.reason == "v3_profile_reliable_hard_block"

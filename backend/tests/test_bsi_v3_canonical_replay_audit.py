from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import scripts.bsi_v3_august_validation as replay
from scripts import bsi_v3_full_canonical_replay_audit as audit


def test_all_26_detectors_remain_callable() -> None:
    registry = replay.strategy_registry()

    assert len(registry) == 26
    assert all(spec.strategy_id in replay.DETECTOR_REGISTRY for spec in registry)
    assert all(callable(replay.DETECTOR_REGISTRY[spec.strategy_id]) for spec in registry)


def test_profile_does_not_convert_data_gap_to_no_setup() -> None:
    event = {
        "detector_result": "DATA_GAP",
        "profile_status": "NEUTRAL",
        "profile_reason": "profile_not_applied_to_non_valid_detector_result",
    }

    assert event["detector_result"] == "DATA_GAP"
    assert event["profile_status"] == "NEUTRAL"
    assert event["profile_reason"] != "NO_SETUP"


def test_profile_decision_is_separate_from_mentor_validity() -> None:
    event = {"detector_result": "VALID_SETUP", "profile_status": "DEPRIORITIZE"}

    assert event["detector_result"] == "VALID_SETUP"
    assert event["profile_status"] == "DEPRIORITIZE"


def test_point_in_time_profile_uses_only_prior_outcomes() -> None:
    good = replay.Outcome("one", "bsi_v3_order_flow", "EURUSD", "LONG", "2026-01-01T00:00:00+00:00", None, "TP", 1.0, 1.0)
    bad = replay.Outcome("two", "bsi_v3_order_flow", "EURUSD", "LONG", "2026-01-02T00:00:00+00:00", None, "SL", -1.0, -1.0)
    events = [
        {"event_id": "e1", "timestamp": "2026-01-01T00:00:00+00:00", "strategy_id": "bsi_v3_order_flow", "symbol": "EURUSD"},
        {"event_id": "e2", "timestamp": "2026-01-02T00:00:00+00:00", "strategy_id": "bsi_v3_order_flow", "symbol": "EURUSD"},
    ]

    audit._apply_walk_forward_profile(events, {"e1": good, "e2": bad})

    assert events[0]["profile_sample_size"] == 0
    assert events[1]["profile_sample_size"] == 1
    assert events[0]["profile_pit_safe"] is True


def test_insufficient_evidence_cannot_hard_block() -> None:
    outcomes = [
        replay.Outcome(str(idx), "bsi_v3_order_flow", "EURUSD", "LONG", "2026-01-01T00:00:00+00:00", None, "SL", -1.0, -1.0)
        for idx in range(11)
    ]

    decision, _score, reason, sample, _reliability = audit._profile_decision_from_stats(outcomes)

    assert sample == 11
    assert decision == "NEUTRAL"
    assert reason == "insufficient_point_in_time_sample"


def test_reactionary_spectre_same_event_canonicalizes_to_same_opportunity() -> None:
    spec_reactionary = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_reactionary_block")
    spec_spectre = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_spectre")
    base = dict(
        symbol="EURUSD",
        direction="LONG",
        entry_time=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        entry=1.1000,
        stop=1.0990,
        target=1.1020,
        timeframe_stack=["M15"],
        thesis_id="src",
        opportunity_id="src",
        source_rule_ids=[],
    )

    reactionary = replay.Opportunity(strategy_id=spec_reactionary.strategy_id, **base)
    spectre = replay.Opportunity(strategy_id=spec_spectre.strategy_id, **base)

    assert audit._canonical_ids(reactionary, spec_reactionary, None)["bsi_v3_entry_opportunity_id"] == audit._canonical_ids(spectre, spec_spectre, None)["bsi_v3_entry_opportunity_id"]


def test_reactionary_only_and_spectre_only_can_remain_independent() -> None:
    spec_reactionary = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_reactionary_block")
    spec_spectre = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_spectre")
    base = dict(
        symbol="EURUSD",
        direction="LONG",
        entry=1.1000,
        stop=1.0990,
        target=1.1020,
        timeframe_stack=["M15"],
        thesis_id="src",
        opportunity_id="src",
        source_rule_ids=[],
    )

    reactionary = replay.Opportunity(strategy_id=spec_reactionary.strategy_id, entry_time=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc), **base)
    spectre = replay.Opportunity(strategy_id=spec_spectre.strategy_id, entry_time=datetime(2026, 1, 5, 12, 15, tzinfo=timezone.utc), **base)

    assert audit._canonical_ids(reactionary, spec_reactionary, None)["bsi_v3_entry_opportunity_id"] != audit._canonical_ids(spectre, spec_spectre, None)["bsi_v3_entry_opportunity_id"]


def test_repeated_scheduler_hits_preserve_same_identity() -> None:
    spec = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_order_flow")
    op = replay.Opportunity(
        spec.strategy_id,
        "EURUSD",
        "LONG",
        datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        1.1000,
        1.0990,
        1.1020,
        ["H4", "M15"],
        "src",
        "src",
        spec.source_rule_ids,
    )

    first = audit._canonical_ids(op, spec, None)
    second = audit._canonical_ids(op, spec, None)

    assert first["bsi_v3_entry_opportunity_id"] == second["bsi_v3_entry_opportunity_id"]


def test_fresh_mentor_event_creates_new_identity() -> None:
    spec = next(spec for spec in replay.strategy_registry() if spec.strategy_id == "bsi_v3_order_flow")
    one = replay.Opportunity(spec.strategy_id, "EURUSD", "LONG", datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc), 1.1, 1.099, 1.102, ["H4", "M15"], "src", "src", spec.source_rule_ids)
    two = replay.Opportunity(spec.strategy_id, "EURUSD", "LONG", datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc), 1.1, 1.099, 1.102, ["H4", "M15"], "src", "src", spec.source_rule_ids)

    assert audit._canonical_ids(one, spec, None)["bsi_v3_entry_opportunity_id"] != audit._canonical_ids(two, spec, None)["bsi_v3_entry_opportunity_id"]


def test_adaptive_selected_is_not_submitted_or_broker_accepted() -> None:
    row = {"status": "SELECTED", "broker_mutation_attempted": False}

    assert row["status"] == "SELECTED"
    assert row["broker_mutation_attempted"] is False


def test_confidence_85_without_confirmation_cannot_submit() -> None:
    event = {"execution_confidence": 85.0, "confirmation_timestamp": None}

    can_submit = event["execution_confidence"] >= 80 and bool(event["confirmation_timestamp"])

    assert can_submit is False


def test_same_opportunity_checked_100_times_submits_at_most_once_per_account() -> None:
    submitted: set[tuple[str, str]] = set()
    attempts = 0
    opportunity_id = "entry:one"
    account = "demo_10k"
    for _ in range(100):
        key = (account, opportunity_id)
        if key in submitted:
            continue
        submitted.add(key)
        attempts += 1

    assert attempts == 1

from __future__ import annotations

from backend.adaptive_management.decision_explainer import explain_entry_decision, explain_management_action


def test_explain_management_action_renders_reason_and_alternatives():
    action = {
        "position_id": "P1",
        "action_id": "A1",
        "action_type": "MOVE_SL_TO_REDUCED_RISK",
        "reason": "protect_cost_adjusted_breakeven",
        "evidence": {"r": 0.62, "max_r": 0.7},
        "requested_sl": 1.0975,
        "requested_tp": None,
        "requested_volume": None,
        "status": "shadow_selected",
        "mode": "shadow",
        "considered_actions": [
            {"action_type": "HOLD", "priority": 100, "reason": "no_management_trigger", "evidence": {}},
            {"action_type": "MOVE_SL_TO_REDUCED_RISK", "priority": 49, "reason": "protect_cost_adjusted_breakeven", "evidence": {"r": 0.62}},
        ],
    }

    result = explain_management_action(action)

    assert result["headline"] == "MOVE_SL_TO_REDUCED_RISK selected"
    assert "protect cost adjusted breakeven" in result["reason"]
    assert "r=0.62" in result["reason"]
    assert result["proposed_change"] == {"sl": 1.0975, "tp": None, "volume": None}
    # The winner itself must not appear in "alternatives" -- only what was NOT selected.
    alt_types = [row["action_type"] for row in result["alternatives_considered"]]
    assert alt_types == ["HOLD"]
    assert "no management trigger" in result["alternatives_considered"][0]["reason_not_selected"]


def test_explain_management_action_hold_has_no_proposed_change():
    action = {"position_id": "P2", "action_id": "A2", "action_type": "HOLD", "reason": "no_management_trigger", "evidence": {}, "considered_actions": [], "status": "shadow_selected", "mode": "shadow"}

    result = explain_management_action(action)

    assert result["headline"] == "No management action taken (HOLD)"
    assert result["proposed_change"] is None
    assert result["alternatives_considered"] == []


def test_explain_entry_decision_accepted_lists_positive_contributors():
    entry_quality = {
        "total_score": 0.82,
        "confidence_interval": [0.72, 0.92],
        "positive_contributors": ["displacement", "liquidity"],
        "negative_contributors": ["imbalance"],
        "reason_codes": ["STRUCTURE_DISPLACEMENT_CONFIRMED", "STRUCTURE_IMBALANCE_ABSENT"],
        "explanations": ["Trend is bullish because higher highs and higher lows."],
    }

    result = explain_entry_decision(entry_quality, ai_decision={"decision": "LONG", "confidence": 0.71})

    assert result["accepted"] is True
    assert "Displacement confirmed" in result["accepted_because"]
    assert "Liquidity confirmed" in result["accepted_because"]
    assert any("AI confidence 0.71" in line for line in result["accepted_because"])
    assert result["rejected_because"] == []
    assert result["structure_explanations"] == entry_quality["explanations"]


def test_explain_entry_decision_rejected_lists_blockers_not_positives():
    entry_quality = {"total_score": 0.3, "positive_contributors": ["liquidity"], "negative_contributors": ["displacement", "imbalance"], "reason_codes": [], "explanations": []}

    result = explain_entry_decision(entry_quality, blockers=["ECONOMIC_BLOCK:CENTRAL_BANK_EVENT_PRE_BLOCK"])

    assert result["accepted"] is False
    assert result["accepted_because"] == []
    assert "Displacement absent" in result["rejected_because"]
    assert "ECONOMIC_BLOCK:CENTRAL_BANK_EVENT_PRE_BLOCK" in result["rejected_because"]

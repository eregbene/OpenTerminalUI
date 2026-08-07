from __future__ import annotations

from typing import Any

# Evidence keys worth surfacing in a rendered reason, in a stable preferred order. Deliberately a
# fixed allowlist (not "dump every evidence key") so the rendered sentence stays readable rather
# than becoming a raw JSON echo.
_EVIDENCE_HIGHLIGHT_KEYS = ("r", "max_r", "floor_r", "protect_fraction", "giveback_r", "allowance_r", "tp_progress")


def _humanize(text: str) -> str:
    return (text or "no_reason_recorded").replace("_", " ").strip()


def _round(value: Any) -> Any:
    return round(value, 4) if isinstance(value, float) else value


def _reason_sentence(reason: str, evidence: dict[str, Any] | None) -> str:
    sentence = _humanize(reason)
    bits = [f"{key.replace('_', ' ')}={_round((evidence or {})[key])}" for key in _EVIDENCE_HIGHLIGHT_KEYS if evidence and evidence.get(key) is not None]
    return f"{sentence} ({', '.join(bits)})" if bits else sentence


def explain_management_action(action: dict[str, Any]) -> dict[str, Any]:
    """Part 8 Decision Explainer for a single management action -- pure rendering over data
    AdaptiveManagementActionORM already stores (reason/evidence/considered_actions), no new
    computation. Every candidate the manager weighed (not just the winner) is included, so "why
    NOT the alternatives" is answered alongside "why this one"."""
    action_type = action.get("action_type") or "UNKNOWN"
    considered = action.get("considered_actions") or []
    headline = "No management action taken (HOLD)" if action_type == "HOLD" else f"{action_type} selected"
    alternatives = [
        {
            "action_type": candidate.get("action_type"),
            "reason_not_selected": _reason_sentence(candidate.get("reason", ""), candidate.get("evidence")),
            "priority": candidate.get("priority"),
        }
        for candidate in considered
        if candidate.get("action_type") != action_type
    ]
    proposed_change = None
    if any(action.get(key) is not None for key in ("requested_sl", "requested_tp", "requested_volume")):
        proposed_change = {"sl": action.get("requested_sl"), "tp": action.get("requested_tp"), "volume": action.get("requested_volume")}
    return {
        "position_id": action.get("position_id"),
        "action_id": action.get("action_id"),
        "headline": headline,
        "reason": _reason_sentence(action.get("reason", ""), action.get("evidence")),
        "alternatives_considered": alternatives,
        "proposed_change": proposed_change,
        "status": action.get("status"),
        "mode": action.get("mode"),
    }


def explain_entry_decision(entry_quality: dict[str, Any], *, ai_decision: dict[str, Any] | None = None, blockers: list[str] | None = None) -> dict[str, Any]:
    """Part 8 Decision Explainer for an entry -- pure rendering over Part 2's already-computed
    entry_quality_score() output (positive/negative structural contributors, reason codes, the
    structure engine's own explanations) plus any blockers that actually rejected the trade.
    Never recomputes the score itself."""
    accepted = not blockers
    positive = entry_quality.get("positive_contributors") or []
    negative = entry_quality.get("negative_contributors") or []
    accepted_because = [f"{name.replace('_', ' ').title()} confirmed" for name in positive]
    rejected_because = [f"{name.replace('_', ' ').title()} absent" for name in negative] + list(blockers or [])
    if ai_decision and ai_decision.get("decision") not in (None, "NO_TRADE"):
        accepted_because.append(f"AI confidence {ai_decision.get('confidence')} on {ai_decision.get('decision')}")
    return {
        "accepted": accepted,
        "total_score": entry_quality.get("total_score"),
        "confidence_interval": entry_quality.get("confidence_interval"),
        "accepted_because": accepted_because if accepted else [],
        "rejected_because": rejected_because if not accepted else [],
        "structure_explanations": entry_quality.get("explanations") or [],
        "reason_codes": entry_quality.get("reason_codes") or [],
    }

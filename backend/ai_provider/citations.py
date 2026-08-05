from __future__ import annotations

from backend.ai_assistant.models import EvidenceBundle


LABELS = {
    "research_run": "Research #1",
    "scorecard": "Scorecard",
    "candidate": "Candidate",
    "deployment": "Deployment",
    "risk_evaluation": "Risk Evaluation",
    "paper_order": "Order",
    "paper_fill": "Order",
    "position": "Position",
    "account_snapshot": "Position",
    "reconciliation": "Reconciliation",
    "strategy_decision": "Strategy",
    "snapshot": "Market Structure",
}


def citation_label(entity_type: str) -> str:
    return LABELS.get(entity_type, entity_type.replace("_", " ").title())


def citations_for_bundle(bundle: EvidenceBundle | dict) -> list[dict[str, str]]:
    bundle_id = bundle.bundle_id if isinstance(bundle, EvidenceBundle) else str(bundle.get("bundle_id"))
    items = bundle.items if isinstance(bundle, EvidenceBundle) else bundle.get("items", [])
    out: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        entity = item.entity if hasattr(item, "entity") else str(item.get("entity"))
        label = citation_label(entity)
        out.append({"label": label, "marker": f"[{label}]", "bundle_id": bundle_id, "index": str(index)})
    return out


def append_default_citation(text: str, citations: list[dict[str, str]]) -> str:
    if not citations:
        return "I don't have sufficient verified evidence to answer."
    if any(c["marker"] in text for c in citations):
        return text
    return f"{text.strip()} {citations[0]['marker']}"

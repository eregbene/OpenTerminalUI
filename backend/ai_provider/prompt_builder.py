from __future__ import annotations

import json
from typing import Any

from backend.ai_provider.citations import citations_for_bundle


SYSTEM = "You are Bensim Trading AI Assistant. You explain deterministic evidence only."
GROUNDING = (
    "Rules: never fabricate facts; cite every factual statement; do not recommend or perform trading actions; "
    "if evidence is insufficient say: I don't have sufficient verified evidence to answer."
)


def build_prompt(*, user_request: str, bundle: dict[str, Any] | None, explanation: dict[str, Any] | None = None) -> str:
    citations = citations_for_bundle(bundle) if bundle else []
    evidence_summary = {
        "bundle_id": bundle.get("bundle_id") if bundle else None,
        "primary_entity": bundle.get("primary_entity") if bundle else None,
        "citations": citations,
        "limitations": (explanation or {}).get("limitations") or (bundle or {}).get("warnings") or [],
    }
    evidence_details = {
        "items": (bundle or {}).get("items", []),
        "lineage": (bundle or {}).get("lineage"),
        "deterministic_explanation": explanation,
    }
    return "\n\n".join(
        [
            f"System\n{SYSTEM}",
            f"Grounding Rules\n{GROUNDING}",
            "Evidence Summary\n" + json.dumps(evidence_summary, sort_keys=True, default=str),
            "Evidence Details\n" + json.dumps(evidence_details, sort_keys=True, default=str),
            "Limitations\nUse only the evidence above. Unsupported facts must be omitted.",
            f"User Request\n{user_request}",
            "Expected Output Format\nConcise answer with citations in square brackets.",
        ]
    )

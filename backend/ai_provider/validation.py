from __future__ import annotations

import re
from typing import Any

from backend.ai_provider.citations import append_default_citation, citations_for_bundle


INSUFFICIENT = "I don't have sufficient verified evidence to answer."
PROHIBITED = re.compile(r"\b(submit|execute|cancel|approve|modify|place)\b.*\b(order|trade|risk|deployment|position|portfolio|strategy)\b", re.I)
NUMERIC = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?")


def validate_output(text: str, *, bundle: dict[str, Any] | None) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not bundle or not bundle.get("items"):
        return INSUFFICIENT, ["missing_evidence"]
    evidence_blob = str(bundle)
    if PROHIBITED.search(text):
        warnings.append("prohibited_action_language_removed")
        text = INSUFFICIENT
    for claim in NUMERIC.findall(text):
        if claim not in evidence_blob:
            warnings.append("unsupported_numeric_claim")
            text = INSUFFICIENT
            break
    for claim in TIMESTAMP.findall(text):
        if claim not in evidence_blob:
            warnings.append("unsupported_timestamp_claim")
            text = INSUFFICIENT
            break
    citations = citations_for_bundle(bundle)
    return append_default_citation(text, citations), warnings

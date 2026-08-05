from __future__ import annotations

from backend.ai_assistant.models import EvidenceItem, Freshness, Quality


class GroundingError(ValueError):
    pass


class GroundingService:
    def require_supported(self, evidence: EvidenceItem) -> None:
        if evidence.quality in {Quality.MISSING, Quality.INVALID}:
            raise GroundingError(f"missing evidence for {evidence.entity}")

    def limitations(self, evidence: EvidenceItem) -> tuple[str, ...]:
        limitations: list[str] = []
        if evidence.freshness in {Freshness.STALE, Freshness.SUPERSEDED, Freshness.INCOMPLETE, Freshness.UNKNOWN}:
            limitations.append(f"freshness:{evidence.freshness.value}")
        if evidence.quality in {Quality.PARTIAL, Quality.FALLBACK, Quality.SIMULATED, Quality.DELAYED, Quality.UNKNOWN}:
            limitations.append(f"quality:{evidence.quality.value}")
        limitations.extend(evidence.warnings)
        return tuple(dict.fromkeys(limitations))

    def guardrails(self) -> dict[str, object]:
        return {
            "deterministic": True,
            "llm_used": False,
            "read_only": True,
            "execution_authority": "none",
            "requires_human_review": True,
            "grounding_required": True,
        }


grounding_service = GroundingService()

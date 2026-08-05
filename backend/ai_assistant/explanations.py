from __future__ import annotations

from typing import Any

from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.grounding import GroundingError, grounding_service
from backend.ai_assistant.models import AssistantIntent, EvidenceItem, Explanation, ExplanationDomain, Freshness


DOMAIN_TO_SOURCE = {
    ExplanationDomain.MARKET_STRUCTURE: "market_structure",
    ExplanationDomain.STRATEGY: "strategy_decision",
    ExplanationDomain.RESEARCH: "research_run",
    ExplanationDomain.RISK: "risk_evaluation",
    ExplanationDomain.ORDER: "paper_order",
    ExplanationDomain.POSITION: "position",
    ExplanationDomain.RECONCILIATION: "reconciliation",
}


class ExplanationService:
    def explain(
        self,
        *,
        domain: ExplanationDomain,
        intent: AssistantIntent,
        entity_id: str | None,
        payload: dict[str, Any],
        evidence_item: EvidenceItem | None = None,
    ) -> Explanation:
        source = DOMAIN_TO_SOURCE[domain]
        evidence = evidence_item or evidence_builder.build(
            source=source,
            entity=domain.value,
            entity_id=entity_id,
            values=payload,
            domain=domain,
        )
        grounding_service.require_supported(evidence)
        guardrails = grounding_service.guardrails()
        guardrails["limitations"] = list(grounding_service.limitations(evidence))
        return Explanation(
            domain=domain,
            intent=intent,
            entity_id=entity_id,
            summary=self._summary(domain, evidence),
            points=tuple(self._points(domain, evidence)),
            evidence=(evidence,),
            guardrails=guardrails,
        )

    def _summary(self, domain: ExplanationDomain, evidence: EvidenceItem) -> str:
        label = domain.value.replace("_", " ")
        if evidence.freshness is Freshness.STALE:
            return f"{label.title()} evidence is stale; review the latest deterministic state before acting."
        return f"{label.title()} explanation is based on deterministic {evidence.source} evidence."

    def _points(self, domain: ExplanationDomain, evidence: EvidenceItem) -> list[str]:
        values = evidence.values
        keys_by_domain = {
            ExplanationDomain.MARKET_STRUCTURE: ("trend", "bias", "event", "timeframe", "confidence", "status"),
            ExplanationDomain.STRATEGY: ("strategy_id", "decision", "status", "reason", "score", "sharpe"),
            ExplanationDomain.RESEARCH: ("run_id", "status", "score", "scorecard", "candidate_id", "lineage_id"),
            ExplanationDomain.RISK: ("status", "risk_score", "limit", "exposure", "drawdown", "reason"),
            ExplanationDomain.ORDER: ("order_id", "symbol", "side", "quantity", "status", "risk_status"),
            ExplanationDomain.POSITION: ("symbol", "quantity", "avg_price", "market_value", "pnl", "exposure"),
            ExplanationDomain.RECONCILIATION: ("status", "break_count", "matched", "unmatched", "as_of"),
        }
        points = [f"{key}: {values[key]}" for key in keys_by_domain[domain] if key in values and values[key] is not None]
        if evidence.freshness is Freshness.STALE:
            points.append("freshness: stale")
        return points or ["No recognized fields were available in the deterministic evidence payload."]


explanation_service = ExplanationService()

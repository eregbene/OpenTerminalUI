from __future__ import annotations

from backend.ai_assistant.models import AssistantIntent, ExplanationDomain


DOMAIN_TOOL = {
    ExplanationDomain.MARKET_STRUCTURE: "get_market_structure_snapshot",
    ExplanationDomain.STRATEGY: "get_strategy_decision",
    ExplanationDomain.RESEARCH: "get_research_run",
    ExplanationDomain.RISK: "get_risk_evaluation",
    ExplanationDomain.ORDER: "get_order",
    ExplanationDomain.POSITION: "get_position",
    ExplanationDomain.RECONCILIATION: "get_reconciliation",
}


def plan_tools(intent: AssistantIntent, domain: ExplanationDomain) -> list[str]:
    if intent is AssistantIntent.COMPARE:
        return [DOMAIN_TOOL[domain]]
    if intent in set(AssistantIntent):
        return [DOMAIN_TOOL[domain]]
    return []

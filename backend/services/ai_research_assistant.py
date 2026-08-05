from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PROHIBITED_ACTIONS = [
    "submit_orders",
    "approve_risk",
    "modify_paper_accounts",
    "promote_strategy",
    "change_strategy_logic",
    "bypass_deterministic_services",
]


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source: str
    summary: str
    data: dict[str, Any]


class AIResearchAssistantService:
    """Deterministic decision-support layer for AI-facing research briefs."""

    async def build_research_brief(
        self,
        *,
        symbol: str,
        horizon: str = "swing",
        question: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol is required")

        context = context or {}
        evidence = self._collect_context_evidence(normalized_symbol, context)
        posture = self._derive_posture(evidence)

        return {
            "type": "research_brief",
            "symbol": normalized_symbol,
            "horizon": horizon,
            "question": question or f"Review {normalized_symbol}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": self._summary(normalized_symbol, posture, evidence),
            "decision_support": {
                "posture": posture,
                "confidence": self._confidence(evidence),
                "next_research_actions": self._next_actions(evidence),
                "not_in_scope": PROHIBITED_ACTIONS,
            },
            "sections": self._sections(evidence),
            "evidence": [item.__dict__ for item in evidence],
            "guardrails": {
                "execution_authority": "none",
                "requires_human_review": True,
                "deterministic_sources_only": True,
            },
        }

    def _collect_context_evidence(self, symbol: str, context: dict[str, Any]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []

        quote = context.get("quote")
        if isinstance(quote, dict):
            items.append(
                EvidenceItem(
                    id="quote.context",
                    source="provided_context.quote",
                    summary=f"{symbol} quote snapshot supplied by the caller.",
                    data=quote,
                )
            )

        market_structure = context.get("market_structure")
        if isinstance(market_structure, dict):
            items.append(
                EvidenceItem(
                    id="market_structure.context",
                    source="provided_context.market_structure",
                    summary="Deterministic market-structure state supplied by the caller.",
                    data=market_structure,
                )
            )

        risk = context.get("risk")
        if isinstance(risk, dict):
            items.append(
                EvidenceItem(
                    id="risk.context",
                    source="provided_context.risk",
                    summary="Risk snapshot supplied by the caller.",
                    data=risk,
                )
            )

        strategy = context.get("strategy")
        if isinstance(strategy, dict):
            items.append(
                EvidenceItem(
                    id="strategy.context",
                    source="provided_context.strategy",
                    summary="Strategy validation snapshot supplied by the caller.",
                    data=strategy,
                )
            )

        return items

    def _derive_posture(self, evidence: list[EvidenceItem]) -> str:
        risk_flags = 0
        support_flags = 0

        for item in evidence:
            payload = item.data
            trend = str(payload.get("trend") or payload.get("bias") or "").lower()
            if trend in {"bullish", "uptrend", "supportive"}:
                support_flags += 1
            if trend in {"bearish", "downtrend", "risk_off"}:
                risk_flags += 1

            risk_score = payload.get("risk_score")
            if isinstance(risk_score, (int, float)):
                if risk_score >= 0.7:
                    risk_flags += 1
                elif risk_score <= 0.3:
                    support_flags += 1

            status = str(payload.get("status") or "").lower()
            if status in {"failed", "invalid", "blocked"}:
                risk_flags += 1
            elif status in {"passed", "validated"}:
                support_flags += 1

        if risk_flags > support_flags:
            return "cautious"
        if support_flags > risk_flags:
            return "constructive"
        return "neutral"

    def _confidence(self, evidence: list[EvidenceItem]) -> str:
        count = len(evidence)
        if count >= 4:
            return "medium"
        if count >= 2:
            return "low-medium"
        return "low"

    def _summary(self, symbol: str, posture: str, evidence: list[EvidenceItem]) -> str:
        if not evidence:
            return f"{symbol} has no supplied deterministic evidence yet; treat any AI commentary as exploratory only."
        return f"{symbol} research posture is {posture} based on {len(evidence)} supplied deterministic evidence item(s)."

    def _sections(self, evidence: list[EvidenceItem]) -> list[dict[str, Any]]:
        if not evidence:
            return [
                {
                    "title": "Evidence Gap",
                    "points": ["No quote, market-structure, risk, or strategy-validation evidence was supplied."],
                    "evidence_ids": [],
                }
            ]

        sections: list[dict[str, Any]] = []
        for item in evidence:
            sections.append(
                {
                    "title": item.summary,
                    "points": self._points_for(item.data),
                    "evidence_ids": [item.id],
                }
            )
        return sections

    def _points_for(self, data: dict[str, Any]) -> list[str]:
        points: list[str] = []
        for key in ("last_price", "change_pct", "trend", "bias", "risk_score", "status", "drawdown", "sharpe"):
            value = data.get(key)
            if value is not None:
                points.append(f"{key}: {value}")
        return points or ["Structured evidence supplied; no recognized summary fields were present."]

    def _next_actions(self, evidence: list[EvidenceItem]) -> list[str]:
        present = {item.id for item in evidence}
        actions: list[str] = []
        if "quote.context" not in present:
            actions.append("Attach latest quote or market snapshot.")
        if "market_structure.context" not in present:
            actions.append("Run deterministic market-structure analysis.")
        if "risk.context" not in present:
            actions.append("Review current risk exposure before any trade decision.")
        if "strategy.context" not in present:
            actions.append("Attach strategy validation or backtest evidence if strategy action is being considered.")
        return actions or ["Review evidence with a human decision maker before taking action."]


_service: AIResearchAssistantService | None = None


def get_ai_research_assistant_service() -> AIResearchAssistantService:
    global _service
    if _service is None:
        _service = AIResearchAssistantService()
    return _service

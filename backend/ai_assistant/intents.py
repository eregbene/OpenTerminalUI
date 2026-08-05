from __future__ import annotations

from backend.ai_assistant.models import AssistantIntent


_KEYWORDS: tuple[tuple[AssistantIntent, tuple[str, ...]], ...] = (
    (AssistantIntent.TRACE_LINEAGE, ("trace", "lineage", "provenance", "source")),
    (AssistantIntent.REVIEW_RISK, ("risk", "limit", "exposure", "drawdown")),
    (AssistantIntent.REVIEW_RESEARCH, ("research", "backtest", "scorecard", "candidate")),
    (AssistantIntent.REVIEW_STRATEGY, ("strategy", "signal", "rule", "decision")),
    (AssistantIntent.REVIEW_TRADING, ("order", "fill", "position", "account", "reconciliation")),
    (AssistantIntent.COMPARE, ("compare", "versus", " vs ", "difference")),
    (AssistantIntent.INVESTIGATE, ("investigate", "why", "diagnose", "inspect")),
    (AssistantIntent.SUMMARIZE, ("summarize", "summary", "brief")),
    (AssistantIntent.EXPLAIN, ("explain", "what happened", "describe")),
)


def parse_intent(text: str | None, default: AssistantIntent = AssistantIntent.EXPLAIN) -> AssistantIntent:
    normalized = f" {(text or '').strip().lower()} "
    if not normalized.strip():
        return default
    for intent, keywords in _KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return intent
    return default

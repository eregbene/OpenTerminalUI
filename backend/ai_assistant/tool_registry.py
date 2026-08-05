from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.ai_assistant.authorization import AuthorizationContext, authorization_service
from backend.ai_assistant.adapters.market_structure import MarketStructureEvidenceAdapter
from backend.ai_assistant.adapters.research import ResearchEvidenceAdapter
from backend.ai_assistant.adapters.strategy import StrategyEvidenceAdapter
from backend.ai_assistant.adapters.trading import TradingEvidenceAdapter
from backend.ai_assistant.models import AssistantIntent, AuthorizationDecision, EntityReference, ToolAuthorization, ToolResult, ToolSpec
from backend.services.ai_research_assistant import PROHIBITED_ACTIONS


READ_INTENTS = tuple(AssistantIntent)


def _schema(entity_name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "entity": {"type": "object"},
            "context": {"type": "object"},
        },
        "required": [],
        "additionalProperties": False,
        "description": f"Read-only {entity_name} lookup from deterministic service output supplied to the assistant.",
    }


def _tool(name: str, description: str, *, max_results: int = 1, timeout_seconds: float = 2.0) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=_schema(name),
        authorization=ToolAuthorization(
            read_only=True,
            allowed_intents=READ_INTENTS,
            denied_capabilities=tuple(PROHIBITED_ACTIONS),
        ),
        timeout_seconds=timeout_seconds,
        max_results=max_results,
        read_only=True,
    )


class AIToolRegistry:
    def __init__(self) -> None:
        self._tools = {
            spec.name: spec
            for spec in (
                _tool("get_market_structure_snapshot", "Read deterministic market-structure analysis."),
                _tool("get_strategy_decision", "Read strategy decision or proposal state."),
                _tool("get_research_run", "Read research run summary."),
                _tool("get_scorecard", "Read research scorecard gate results."),
                _tool("get_candidate", "Read research candidate metadata."),
                _tool("get_deployment", "Read deployment state without approving or mutating it."),
                _tool("get_risk_evaluation", "Read risk evaluation output."),
                _tool("get_order", "Read paper order details."),
                _tool("get_fills", "Read paper fill details.", max_results=100),
                _tool("get_position", "Read position snapshot."),
                _tool("get_account_snapshot", "Read account snapshot."),
                _tool("get_reconciliation", "Read reconciliation summary."),
            )
        }
        self._adapters = {
            "get_market_structure_snapshot": MarketStructureEvidenceAdapter(),
            "get_strategy_decision": StrategyEvidenceAdapter(),
            "get_research_run": ResearchEvidenceAdapter(),
            "get_scorecard": ResearchEvidenceAdapter(),
            "get_candidate": ResearchEvidenceAdapter(),
            "get_deployment": TradingEvidenceAdapter(),
            "get_risk_evaluation": TradingEvidenceAdapter(),
            "get_order": TradingEvidenceAdapter(),
            "get_fills": TradingEvidenceAdapter(),
            "get_position": TradingEvidenceAdapter(),
            "get_account_snapshot": TradingEvidenceAdapter(),
            "get_reconciliation": TradingEvidenceAdapter(),
        }

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unsupported AI tool: {name}") from exc

    def execute_read(
        self,
        name: str,
        *,
        reference: EntityReference,
        auth_context: AuthorizationContext,
    ) -> ToolResult:
        started = time.perf_counter()
        spec = self.get(name)
        tool_call_id = f"tool_{uuid4().hex[:12]}"
        try:
            authorization_service.authorize_tool(spec, auth_context)
        except PermissionError:
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=name,
                entity_reference=reference,
                evidence_item=None,
                retrieved_at=datetime.now(timezone.utc),
                authorization_result=AuthorizationDecision.DENIED,
                duration_ms=(time.perf_counter() - started) * 1000,
                warnings=("tool authorization denied",),
            )
        adapter = self._adapters[name]
        result = adapter.get_entity(reference, authorization_context=auth_context)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name=name,
            entity_reference=reference,
            evidence_item=result.evidence,
            retrieved_at=datetime.now(timezone.utc),
            authorization_result=result.authorization_result,
            duration_ms=(time.perf_counter() - started) * 1000,
            warnings=result.warnings,
        )


tool_registry = AIToolRegistry()

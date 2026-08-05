from __future__ import annotations

from typing import Any

from backend.ai_assistant.audit import audit_log
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.bundles import bundle_service
from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.explanations import explanation_service
from backend.ai_assistant.intents import parse_intent
from backend.ai_assistant.lineage import lineage_service
from backend.ai_assistant.models import (
    AssistantIntent,
    AuthorizationDecision,
    EntityReference,
    ExplanationDomain,
    SourcePersistence,
    ToolResult,
)
from backend.ai_assistant.security import SafeAPIError, validate_identifier
from backend.ai_assistant.security import filter_evidence_values
from backend.ai_assistant.tool_registry import tool_registry


class AIAssistantService:
    def explain(
        self,
        *,
        domain: ExplanationDomain,
        payload: dict[str, Any],
        user_id: str | None = None,
        user: Any | None = None,
        endpoint: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        intent = self._intent_for(domain, payload)
        reference = self._reference_for(domain, payload)
        auth_context = self._auth_context(user_id=user_id, intent=intent, payload=payload, user=user)
        tool_name = self._tool_for_reference(reference)
        tool_result = tool_registry.execute_read(
            tool_name,
            reference=reference,
            auth_context=auth_context,
        )
        evidence = tool_result.evidence_item or self._fallback_evidence(domain, reference, payload, tool_result)
        if evidence is None:
            if tool_result.authorization_result in {AuthorizationDecision.DENIED, AuthorizationDecision.SCOPE_MISMATCH}:
                raise LookupError(f"missing {domain.value} evidence")
            raise LookupError(f"missing {domain.value} evidence")
        lineage = lineage_service.trace(reference, auth_context=auth_context)
        missing = list(lineage.missing_links)
        warnings = list(tool_result.warnings)
        bundle = bundle_service.assemble(
            primary_entity=reference,
            items=[evidence],
            lineage=lineage,
            missing_evidence=missing,
            warnings=warnings,
            auth_context=auth_context,
        )
        explanation = explanation_service.explain(
            domain=domain,
            intent=intent,
            entity_id=reference.entity_id,
            payload=evidence.values,
            evidence_item=evidence,
        )
        result = explanation.to_dict()
        result["explanation"] = result["summary"]
        result["tool"] = self._legacy_tool_metadata(tool_result)
        result["tool_call"] = tool_result.to_dict()
        result["evidence_bundle_id"] = bundle.bundle_id
        result["evidence_bundle"] = bundle.to_dict()
        result["freshness"] = evidence.freshness.value
        result["quality"] = evidence.quality.value
        result["limitations"] = result["guardrails"].get("limitations", [])
        result["audit"] = audit_log.record(
            event="ai_assistant.explain",
            payload={
                "domain": domain.value,
                "intent": intent.value,
                "entity": reference.to_dict(),
                "bundle_id": bundle.bundle_id,
            },
            actor=auth_context.user_id,
            endpoint=endpoint,
            correlation_id=correlation_id,
            result_status=tool_result.authorization_result.value,
        )
        return result

    def retrieve_evidence(
        self,
        *,
        payload: dict[str, Any],
        user_id: str | None = None,
        user: Any | None = None,
        endpoint: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        domain = self._domain_from_payload(payload)
        intent = self._intent_for(domain, payload)
        reference = self._reference_for(domain, payload)
        auth_context = self._auth_context(user_id=user_id, intent=intent, payload=payload, user=user)
        tool_name = self._tool_for_reference(reference)
        tool_result = tool_registry.execute_read(
            tool_name,
            reference=reference,
            auth_context=auth_context,
        )
        if tool_result.evidence_item is None:
            raise LookupError("missing evidence")
        lineage = lineage_service.trace(reference, auth_context=auth_context)
        bundle = bundle_service.assemble(
            primary_entity=reference,
            items=[tool_result.evidence_item],
            lineage=lineage,
            missing_evidence=list(lineage.missing_links),
            warnings=list(tool_result.warnings),
            auth_context=auth_context,
        )
        audit = audit_log.record(
            event="ai_assistant.evidence.retrieve",
            payload={"entity": reference.to_dict(), "bundle_id": bundle.bundle_id},
            actor=auth_context.user_id,
            endpoint=endpoint,
            correlation_id=correlation_id,
            result_status=tool_result.authorization_result.value,
        )
        return {"bundle": bundle.to_dict(), "tool_call": tool_result.to_dict(), "audit": audit}

    def trace_lineage(
        self,
        *,
        payload: dict[str, Any],
        user: Any | None = None,
        endpoint: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        domain = self._domain_from_payload(payload)
        reference = self._reference_for(domain, payload)
        auth_context = self._auth_context(user_id=None, intent=AssistantIntent.TRACE_LINEAGE, payload=payload, user=user)
        lineage = lineage_service.trace(reference, auth_context=auth_context)
        audit = audit_log.record(
            event="ai_assistant.evidence.lineage",
            payload={"entity": reference.to_dict()},
            actor=auth_context.user_id,
            endpoint=endpoint,
            correlation_id=correlation_id,
            result_status="ok",
        )
        return {"entity": reference.to_dict(), "lineage": lineage.to_dict(), "audit": audit}

    def _intent_for(self, domain: ExplanationDomain, payload: dict[str, Any]) -> AssistantIntent:
        explicit = payload.get("intent")
        if isinstance(explicit, str):
            try:
                return AssistantIntent[explicit.strip().upper()]
            except KeyError:
                return parse_intent(explicit)
        question = payload.get("question")
        if isinstance(question, str):
            return parse_intent(question)
        defaults = {
            ExplanationDomain.STRATEGY: AssistantIntent.REVIEW_STRATEGY,
            ExplanationDomain.RESEARCH: AssistantIntent.REVIEW_RESEARCH,
            ExplanationDomain.RISK: AssistantIntent.REVIEW_RISK,
            ExplanationDomain.ORDER: AssistantIntent.REVIEW_TRADING,
            ExplanationDomain.POSITION: AssistantIntent.REVIEW_TRADING,
            ExplanationDomain.RECONCILIATION: AssistantIntent.REVIEW_TRADING,
            ExplanationDomain.MARKET_STRUCTURE: AssistantIntent.EXPLAIN,
        }
        return defaults[domain]

    def _domain_from_payload(self, payload: dict[str, Any]) -> ExplanationDomain:
        value = payload.get("domain")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("domain is required")
        try:
            return ExplanationDomain(value.strip())
        except ValueError as exc:
            raise SafeAPIError(422, "INVALID_REFERENCE", "invalid reference") from exc

    def _reference_for(self, domain: ExplanationDomain, payload: dict[str, Any]) -> EntityReference:
        entity_id = self._entity_id(payload)
        if not entity_id:
            raise ValueError("entity_id is required")
        return EntityReference(
            domain=domain,
            entity_type=self._entity_type_for(domain, payload, entity_id),
            entity_id=validate_identifier(entity_id),
            entity_version=self._validated_optional(payload, "entity_version", "version"),
            account_id=self._validated_optional(payload, "account_id"),
            deployment_id=self._validated_optional(payload, "deployment_id"),
            strategy_id=self._validated_optional(payload, "strategy_id"),
        )

    def _entity_type_for(self, domain: ExplanationDomain, payload: dict[str, Any], entity_id: str) -> str:
        explicit = payload.get("entity_type")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        lowered = entity_id.lower()
        if domain is ExplanationDomain.MARKET_STRUCTURE:
            return "snapshot"
        if domain is ExplanationDomain.STRATEGY:
            if "deployment_id" in payload or lowered.startswith("deploy"):
                return "deployment"
            if lowered.startswith("cand"):
                return "candidate"
            return "strategy_decision"
        if domain is ExplanationDomain.RESEARCH:
            if lowered.startswith("score") or "scorecard_id" in payload:
                return "scorecard"
            if lowered.startswith("cand"):
                return "candidate"
            return "research_run"
        if domain is ExplanationDomain.RISK:
            return "risk_evaluation"
        if domain is ExplanationDomain.ORDER:
            return "paper_fill" if lowered.startswith("fill") else "paper_order"
        if domain is ExplanationDomain.POSITION:
            return "account_snapshot" if lowered.startswith("acct") or payload.get("snapshot") is True else "position"
        return "reconciliation"

    def _tool_for_reference(self, reference: EntityReference) -> str:
        by_type = {
            "snapshot": "get_market_structure_snapshot",
            "strategy_decision": "get_strategy_decision",
            "research_run": "get_research_run",
            "scorecard": "get_scorecard",
            "candidate": "get_candidate",
            "deployment": "get_deployment",
            "risk_evaluation": "get_risk_evaluation",
            "paper_order": "get_order",
            "paper_fill": "get_fills",
            "position": "get_position",
            "account_snapshot": "get_account_snapshot",
            "reconciliation": "get_reconciliation",
        }
        return by_type[reference.entity_type]

    def _auth_context(
        self,
        *,
        user_id: str | None,
        intent: AssistantIntent,
        payload: dict[str, Any],
        user: Any | None = None,
    ) -> AuthorizationContext:
        role = "trader"
        actor = user_id
        if user is not None:
            actor = str(getattr(user, "id", "") or "")
            raw_role = getattr(user, "role", "trader")
            role = str(getattr(raw_role, "value", raw_role) or "trader")
        if not actor:
            actor = user_id
        return AuthorizationContext(
            user_id=actor,
            intent=intent,
            role=role,
            account_ids=(),
            research_owner=actor,
        )

    def _fallback_evidence(
        self,
        domain: ExplanationDomain,
        reference: EntityReference,
        payload: dict[str, Any],
        tool_result: ToolResult,
    ):
        entity = payload.get("entity")
        if not isinstance(entity, dict) or not entity:
            return None
        return evidence_builder.build(
            source=f"caller_supplied.{domain.value}",
            entity=domain.value,
            entity_id=reference.entity_id,
            values=filter_evidence_values(reference.entity_type, entity),
            domain=domain,
            persistence=SourcePersistence.UNSUPPORTED,
            warnings=("caller_supplied_fallback", tool_result.authorization_result.value),
        )

    def _legacy_tool_metadata(self, tool_result: ToolResult) -> dict[str, Any]:
        spec = tool_registry.get(tool_result.tool_name)
        return {
            "name": spec.name,
            "read_only": spec.read_only,
            "authorization_result": tool_result.authorization_result.value,
            "duration_ms": tool_result.duration_ms,
            "warnings": list(tool_result.warnings),
        }

    def _entity_id(self, payload: dict[str, Any]) -> str | None:
        for key in (
            "entity_id",
            "id",
            "symbol",
            "order_id",
            "fill_id",
            "run_id",
            "scorecard_id",
            "candidate_id",
            "deployment_id",
            "risk_evaluation_id",
            "strategy_id",
            "position_id",
            "account_id",
            "reconciliation_id",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _optional(self, payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _validated_optional(self, payload: dict[str, Any], *keys: str) -> str | None:
        value = self._optional(payload, *keys)
        return validate_identifier(value) if value else None


_service: AIAssistantService | None = None


def get_ai_assistant_service() -> AIAssistantService:
    global _service
    if _service is None:
        _service = AIAssistantService()
    return _service

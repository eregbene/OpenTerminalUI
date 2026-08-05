from __future__ import annotations

from backend.ai_assistant.adapters.base import AdapterResult
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.models import AuthorizationDecision, EntityReference, ExplanationDomain, SourcePersistence
from backend.ai_assistant.security import filter_evidence_values


class StrategyEvidenceAdapter:
    domain = ExplanationDomain.STRATEGY

    def get_entity(self, reference: EntityReference, *, authorization_context: AuthorizationContext) -> AdapterResult:
        if reference.entity_type == "deployment":
            return self._deployment(reference, authorization_context)
        if reference.entity_type == "candidate":
            from backend.ai_assistant.adapters.research import ResearchEvidenceAdapter

            return ResearchEvidenceAdapter().get_entity(reference, authorization_context=authorization_context)
        from backend.strategies.registry import get_strategy

        spec = get_strategy(reference.entity_id)
        if spec is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        payload = spec.model_dump(mode="json", by_alias=True)
        version = spec.strategy_hash()
        if reference.entity_version and reference.entity_version not in {spec.strategy.version, version}:
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=("requested strategy version/hash not found",))
        evidence = evidence_builder.build(
            source="strategy.registry",
            entity="strategy_decision",
            entity_id=reference.entity_id,
            values={**payload, "version": version, "status": spec.strategy.status, "strategy_id": spec.strategy.id},
            version=version,
            domain=self.domain,
            persistence=SourcePersistence.PROCESS_LOCAL,
            warnings=("strategy registry is process-local/static code",),
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence, warnings=("strategy registry is process-local/static code",))

    def _deployment(self, reference: EntityReference, authorization_context: AuthorizationContext) -> AdapterResult:
        from backend.trading.persistence import TradingStore

        row = TradingStore().load()["deployments"].get(reference.entity_id)
        if row is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        if not authorization_context.can_read_account_row(row):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        version = str(row.get("version") or "1")
        if reference.entity_version and reference.entity_version != version:
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=("requested deployment version not found",))
        evidence = evidence_builder.build(
            source="trading.deployments",
            entity="deployment",
            entity_id=reference.entity_id,
            values=filter_evidence_values("deployment", row),
            version=version,
            domain=self.domain,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)

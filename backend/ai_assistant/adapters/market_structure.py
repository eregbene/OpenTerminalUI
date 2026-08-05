from __future__ import annotations

from backend.ai_assistant.adapters.base import AdapterResult
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.models import AuthorizationDecision, EntityReference, ExplanationDomain, SourcePersistence
from backend.ai_assistant.security import filter_evidence_values


class MarketStructureEvidenceAdapter:
    domain = ExplanationDomain.MARKET_STRUCTURE

    def get_entity(self, reference: EntityReference, *, authorization_context: AuthorizationContext) -> AdapterResult:
        from backend.api.routes.market_structure import _SNAPSHOTS

        snapshot = _SNAPSHOTS.get(reference.entity_id)
        if snapshot is None:
            return AdapterResult(
                AuthorizationDecision.UNSUPPORTED,
                warnings=("market-structure snapshots are process-local and unavailable after restart",),
            )
        payload = snapshot.model_dump(mode="json")
        if reference.entity_version and reference.entity_version != payload.get("configuration_hash"):
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=("requested version does not match snapshot configuration_hash",))
        evidence = evidence_builder.build(
            source="market_structure.snapshot",
            entity="snapshot",
            entity_id=reference.entity_id,
            values=filter_evidence_values("snapshot", payload),
            version=payload.get("configuration_hash") or "process-local",
            domain=self.domain,
            persistence=SourcePersistence.PROCESS_LOCAL,
            warnings=("process-local source",),
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence, warnings=("process-local source",))

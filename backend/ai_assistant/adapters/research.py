from __future__ import annotations

from backend.ai_assistant.adapters.base import AdapterResult
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.models import AuthorizationDecision, EntityReference, ExplanationDomain, SourcePersistence
from backend.ai_assistant.security import filter_evidence_values
from backend.research.services import get_research_service


BUCKET_BY_TYPE = {
    "research_run": ("backtests", "run_id"),
    "scorecard": ("scorecards", "scorecard_id"),
    "candidate": ("candidates", "candidate_id"),
}


class ResearchEvidenceAdapter:
    domain = ExplanationDomain.RESEARCH

    def get_entity(self, reference: EntityReference, *, authorization_context: AuthorizationContext) -> AdapterResult:
        bucket, id_field = BUCKET_BY_TYPE.get(reference.entity_type, ("backtests", "run_id"))
        row = get_research_service().registry.get(bucket, reference.entity_id)
        if row is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        owner = row.get("owner")
        if not authorization_context.can_read_research(owner):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        version = (
            row.get("manifest_hash")
            or row.get("configuration_hash")
            or row.get("strategy_version")
            or row.get("version")
            or row.get(id_field)
            or "evidence"
        )
        version = str(version)
        if reference.entity_version and reference.entity_version != version:
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=(f"requested {reference.entity_type} version not found",))
        evidence = evidence_builder.build(
            source=f"research.{bucket}",
            entity=reference.entity_type,
            entity_id=reference.entity_id,
            values=filter_evidence_values(reference.entity_type, row),
            version=version,
            domain=self.domain,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)

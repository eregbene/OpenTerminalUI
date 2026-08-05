from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.models import AuthorizationDecision, EntityReference, EvidenceItem, ExplanationDomain


@dataclass(frozen=True)
class AdapterResult:
    authorization_result: AuthorizationDecision
    evidence: EvidenceItem | None = None
    warnings: tuple[str, ...] = ()


class EvidenceAdapter(Protocol):
    domain: ExplanationDomain

    def get_entity(
        self,
        reference: EntityReference,
        *,
        authorization_context: AuthorizationContext,
    ) -> AdapterResult:
        ...

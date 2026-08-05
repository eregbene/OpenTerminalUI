from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Any


class AssistantIntent(str, Enum):
    EXPLAIN = "EXPLAIN"
    SUMMARIZE = "SUMMARIZE"
    COMPARE = "COMPARE"
    INVESTIGATE = "INVESTIGATE"
    TRACE_LINEAGE = "TRACE_LINEAGE"
    REVIEW_RISK = "REVIEW_RISK"
    REVIEW_RESEARCH = "REVIEW_RESEARCH"
    REVIEW_STRATEGY = "REVIEW_STRATEGY"
    REVIEW_TRADING = "REVIEW_TRADING"


class ExplanationDomain(str, Enum):
    MARKET_STRUCTURE = "market_structure"
    STRATEGY = "strategy"
    RESEARCH = "research"
    RISK = "risk"
    ORDER = "order"
    POSITION = "position"
    RECONCILIATION = "reconciliation"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    IMMUTABLE = "IMMUTABLE"
    INCOMPLETE = "INCOMPLETE"


class Quality(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    VALID = "VALID"
    FALLBACK = "FALLBACK"
    SIMULATED = "SIMULATED"
    DELAYED = "DELAYED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class AuthorizationDecision(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    NOT_FOUND = "NOT_FOUND"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    UNSUPPORTED = "UNSUPPORTED"


class SourcePersistence(str, Enum):
    DATABASE = "database-backed"
    FILE = "file-backed"
    PROCESS_LOCAL = "process-local"
    UNSUPPORTED = "unsupported"


ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


@dataclass(frozen=True)
class AssistantRequest:
    intent: AssistantIntent
    domain: ExplanationDomain
    entity_id: str | None = None
    entity: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityReference:
    domain: ExplanationDomain
    entity_type: str
    entity_id: str
    entity_version: str | None = None
    account_id: str | None = None
    deployment_id: str | None = None
    strategy_id: str | None = None
    as_of: datetime | None = None
    process_local: bool = False

    def __post_init__(self) -> None:
        allowed = {
            ExplanationDomain.MARKET_STRUCTURE: {"snapshot"},
            ExplanationDomain.STRATEGY: {"strategy_decision", "deployment", "candidate"},
            ExplanationDomain.RESEARCH: {"research_run", "scorecard", "candidate"},
            ExplanationDomain.RISK: {"risk_evaluation"},
            ExplanationDomain.ORDER: {"paper_order", "paper_fill"},
            ExplanationDomain.POSITION: {"position", "account_snapshot"},
            ExplanationDomain.RECONCILIATION: {"reconciliation"},
        }
        if self.entity_type not in allowed[self.domain]:
            raise ValueError(f"unsupported entity_type {self.entity_type!r} for {self.domain.value}")
        for key, value in {
            "entity_id": self.entity_id,
            "entity_version": self.entity_version,
            "account_id": self.account_id,
            "deployment_id": self.deployment_id,
            "strategy_id": self.strategy_id,
        }.items():
            if value is not None and not ID_PATTERN.match(str(value)):
                raise ValueError(f"malformed {key}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_version": self.entity_version,
            "account_id": self.account_id,
            "deployment_id": self.deployment_id,
            "strategy_id": self.strategy_id,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "process_local": self.process_local,
        }


@dataclass(frozen=True)
class ToolAuthorization:
    read_only: bool
    allowed_intents: tuple[AssistantIntent, ...]
    denied_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    authorization: ToolAuthorization
    timeout_seconds: float
    max_results: int
    read_only: bool = True


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source: str
    entity: str
    version: str
    timestamp: datetime | None
    freshness: Freshness
    quality: Quality
    values: dict[str, Any]
    persistence: SourcePersistence = SourcePersistence.UNSUPPORTED
    content_hash: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "entity": self.entity,
            "version": self.version,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "freshness": self.freshness.value,
            "quality": self.quality.value,
            "structured_values": self.values,
            "persistence": self.persistence.value,
            "content_hash": self.content_hash,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    entity_reference: EntityReference
    evidence_item: EvidenceItem | None
    retrieved_at: datetime
    authorization_result: AuthorizationDecision
    duration_ms: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "entity_reference": self.entity_reference.to_dict(),
            "evidence_item": self.evidence_item.to_dict() if self.evidence_item else None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "authorization_result": self.authorization_result.value,
            "duration_ms": self.duration_ms,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LineageResult:
    nodes: tuple[dict[str, Any], ...] = ()
    edges: tuple[dict[str, Any], ...] = ()
    missing_links: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "missing_links": list(self.missing_links),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EvidenceBundle:
    bundle_id: str
    request_id: str
    primary_entity: EntityReference
    items: tuple[EvidenceItem, ...]
    lineage: LineageResult | None
    missing_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    created_at: datetime
    content_hash: str
    omitted_fields: tuple[str, ...] = ()
    owner_user_id: str | None = None
    authorized_account_ids: tuple[str, ...] = ()
    authorized_research_scope: tuple[str, ...] = ()
    expires_at: datetime | None = None
    truncated: bool = False
    omitted_item_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "request_id": self.request_id,
            "primary_entity": self.primary_entity.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "lineage": self.lineage.to_dict() if self.lineage else None,
            "missing_evidence": list(self.missing_evidence),
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
            "content_hash": self.content_hash,
            "omitted_fields": list(self.omitted_fields),
            "owner_user_id": self.owner_user_id,
            "authorized_account_ids": list(self.authorized_account_ids),
            "authorized_research_scope": list(self.authorized_research_scope),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "truncated": self.truncated,
            "omitted_item_count": self.omitted_item_count,
        }


@dataclass(frozen=True)
class Explanation:
    domain: ExplanationDomain
    intent: AssistantIntent
    entity_id: str | None
    summary: str
    points: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...]
    guardrails: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "intent": self.intent.value,
            "entity_id": self.entity_id,
            "summary": self.summary,
            "points": list(self.points),
            "evidence": [item.to_dict() for item in self.evidence],
            "guardrails": self.guardrails,
        }

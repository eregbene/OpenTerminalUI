from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.models import EntityReference, EvidenceBundle, EvidenceItem, LineageResult
from backend.ai_assistant.repository import repository
from backend.ai_assistant.security import (
    BUNDLE_RETENTION_DAYS,
    MAX_BUNDLE_BYTES,
    MAX_EVIDENCE_ITEMS,
    MAX_VALUE_CHARS,
    SafeAPIError,
    bounded_json_bytes,
    filter_evidence_values,
    sanitize_warnings,
)


class EvidenceBundleService:
    def assemble(
        self,
        *,
        primary_entity: EntityReference,
        items: list[EvidenceItem],
        lineage: LineageResult | None = None,
        missing_evidence: list[str] | None = None,
        warnings: list[str] | None = None,
        auth_context: AuthorizationContext | None = None,
    ) -> EvidenceBundle:
        deduped: list[EvidenceItem] = []
        seen: set[str] = set()
        omitted: list[str] = []
        for item in items:
            key = item.content_hash or item.id
            if key in seen:
                omitted.append(f"duplicate:{item.id}")
                continue
            if len(deduped) >= MAX_EVIDENCE_ITEMS:
                omitted.append(f"item_limit:{item.id}")
                continue
            seen.add(key)
            deduped.append(self._truncate_and_filter(item, omitted))
        raw = json.dumps([item.to_dict() for item in deduped], sort_keys=True, default=str, separators=(",", ":"))
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        created_at = datetime.now(timezone.utc)
        bundle = EvidenceBundle(
            bundle_id=f"bundle_{uuid4().hex[:12]}",
            request_id=f"req_{uuid4().hex[:12]}",
            primary_entity=primary_entity,
            items=tuple(deduped),
            lineage=lineage,
            missing_evidence=tuple(missing_evidence or ()),
            warnings=sanitize_warnings(warnings or ()),
            created_at=created_at,
            content_hash=content_hash,
            omitted_fields=tuple(omitted),
            owner_user_id=auth_context.user_id if auth_context else None,
            authorized_account_ids=auth_context.account_ids if auth_context else (),
            authorized_research_scope=(auth_context.research_owner,) if auth_context and auth_context.research_owner else (),
            expires_at=created_at + timedelta(days=BUNDLE_RETENTION_DAYS),
            truncated=bool(omitted),
            omitted_item_count=max(0, len(items) - len(deduped)),
        )
        if bounded_json_bytes(bundle.to_dict()) > MAX_BUNDLE_BYTES:
            raise SafeAPIError(413, "PAYLOAD_TOO_LARGE", "evidence bundle too large")
        repository.save_bundle(bundle.to_dict())
        return bundle

    def get(self, bundle_id: str, *, auth_context: AuthorizationContext | None = None) -> dict | None:
        bundle = repository.get_bundle(bundle_id)
        if bundle is None:
            return None
        if _expired(bundle):
            return None
        if auth_context and not _can_read_bundle(bundle, auth_context):
            raise PermissionError("bundle access denied")
        return bundle

    def _truncate_and_filter(self, item: EvidenceItem, omitted: list[str]) -> EvidenceItem:
        filtered = filter_evidence_values(item.entity, item.values)
        item = EvidenceItem(
            id=item.id,
            source=item.source,
            entity=item.entity,
            version=item.version,
            timestamp=item.timestamp,
            freshness=item.freshness,
            quality=item.quality,
            values=filtered,
            persistence=item.persistence,
            content_hash=item.content_hash,
            warnings=sanitize_warnings(item.warnings),
        )
        raw = json.dumps(item.values, sort_keys=True, default=str)
        if len(raw) <= MAX_VALUE_CHARS:
            return item
        values = dict(item.values)
        for key in sorted(values, key=lambda k: len(json.dumps(values[k], default=str)), reverse=True):
            if len(json.dumps(values, default=str)) <= MAX_VALUE_CHARS:
                break
            values[key] = "[omitted:large-field]"
            omitted.append(f"{item.id}.{key}")
        return EvidenceItem(
            id=item.id,
            source=item.source,
            entity=item.entity,
            version=item.version,
            timestamp=item.timestamp,
            freshness=item.freshness,
            quality=item.quality,
            values=values,
            persistence=item.persistence,
            content_hash=item.content_hash,
            warnings=item.warnings,
        )


bundle_service = EvidenceBundleService()


def _expired(bundle: dict) -> bool:
    value = bundle.get("expires_at")
    if not isinstance(value, str):
        return False
    try:
        expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= datetime.now(timezone.utc)


def _can_read_bundle(bundle: dict, auth_context: AuthorizationContext) -> bool:
    if auth_context.role == "admin":
        return True
    owner = bundle.get("owner_user_id")
    return bool(owner and owner == auth_context.user_id)

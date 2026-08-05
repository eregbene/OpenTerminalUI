from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.ai_assistant.freshness import domain_freshness, domain_quality
from backend.ai_assistant.models import EvidenceItem, ExplanationDomain, Freshness, Quality, SourcePersistence


STALE_AFTER_SECONDS = 24 * 60 * 60


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def freshness_for(timestamp: datetime | None, now: datetime | None = None) -> Freshness:
    if timestamp is None:
        return Freshness.UNKNOWN
    current = now or datetime.now(timezone.utc)
    age = (current - timestamp).total_seconds()
    return Freshness.STALE if age > STALE_AFTER_SECONDS else Freshness.FRESH


def quality_for(values: dict[str, Any]) -> Quality:
    if not values:
        return Quality.MISSING
    meaningful = [value for value in values.values() if value not in (None, "", [], {})]
    if len(meaningful) == len(values):
        return Quality.COMPLETE
    return Quality.PARTIAL


class EvidenceBuilder:
    def build(
        self,
        *,
        source: str,
        entity: str,
        entity_id: str | None,
        values: dict[str, Any],
        version: str = "v1",
        domain: ExplanationDomain | None = None,
        persistence: SourcePersistence = SourcePersistence.UNSUPPORTED,
        warnings: tuple[str, ...] = (),
    ) -> EvidenceItem:
        timestamp = parse_timestamp(values.get("timestamp") or values.get("as_of") or values.get("created_at"))
        evidence_id = f"{source}:{entity_id or entity}"
        normalized = json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        freshness = domain_freshness(domain, values, timestamp=timestamp) if domain else freshness_for(timestamp)
        quality = domain_quality(domain, values) if domain else quality_for(values)
        return EvidenceItem(
            id=evidence_id,
            source=source,
            entity=entity,
            version=str(values.get("version") or version),
            timestamp=timestamp,
            freshness=freshness,
            quality=quality,
            values=dict(values),
            persistence=persistence,
            content_hash=content_hash,
            warnings=warnings,
        )


evidence_builder = EvidenceBuilder()

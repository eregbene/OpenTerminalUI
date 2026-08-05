from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    version: int = 1
    timestamp: str = Field(default_factory=utc_now_iso)
    source: str
    correlation_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        *,
        source: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> "EventEnvelope":
        return cls(
            event_type=event_type,
            source=source,
            payload=payload or {},
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

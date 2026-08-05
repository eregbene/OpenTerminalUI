from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.ai_assistant.repository import repository
from backend.ai_assistant.security import sanitize_value


class AssistantAuditLog:
    def record(
        self,
        *,
        event: str,
        payload: dict[str, Any],
        actor: str | None = None,
        endpoint: str | None = None,
        correlation_id: str | None = None,
        result_status: str = "ok",
        persist: bool = True,
    ) -> dict[str, Any]:
        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "endpoint": endpoint,
            "correlation_id": correlation_id,
            "result_status": result_status,
            "payload": sanitize_value(payload),
            "mutable_action": False,
        }
        if persist:
            repository.append_audit(record)
        return record


audit_log = AssistantAuditLog()

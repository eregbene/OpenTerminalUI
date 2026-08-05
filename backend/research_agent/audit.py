from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.ai_assistant.security import sanitize_value
from backend.research_agent.store import store


def audit(action: str, *, actor: str, target: str | None = None, result: str = "ok", payload: dict[str, Any] | None = None, correlation_id: str | None = None) -> dict[str, Any]:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "component": "research_agent",
        "action": action,
        "target": target,
        "authorization_decision": result,
        "correlation_id": correlation_id,
        "result": result,
        "mutation_flag": action not in {"view", "status"},
        "payload": sanitize_value(payload or {}),
    }
    raw = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
    record["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    store.append_audit(record)
    return record

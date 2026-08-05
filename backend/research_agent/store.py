from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.ai_assistant.repository import AIAssistantRepository
from backend.ai_assistant.security import sanitize_value, validate_identifier


class ResearchAgentStore:
    def __init__(self, root: Path | str = "data/research_agent") -> None:
        self.repo = AIAssistantRepository(root)
        self.state_path = self.repo.root / "state.json"

    def read(self) -> dict[str, Any]:
        return self.repo._read(
            self.state_path,
            {"policies": {}, "plans": {}, "kill_switches": {}, "reports": {}, "memory": {}, "audit": []},
        )

    def write(self, data: dict[str, Any]) -> None:
        self.repo._write(self.state_path, sanitize_value(data))

    def put(self, collection: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        validate_identifier(key)
        data = self.read()
        raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        value["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        data.setdefault(collection, {})[key] = sanitize_value(value)
        self.write(data)
        return value

    def append_audit(self, record: dict[str, Any]) -> None:
        data = self.read()
        data.setdefault("audit", []).append(sanitize_value(record))
        self.write(data)


store = ResearchAgentStore()

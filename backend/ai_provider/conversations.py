from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.ai_assistant.repository import AIAssistantRepository
from backend.ai_assistant.security import SafeAPIError, sanitize_value, validate_identifier


class ConversationStore:
    def __init__(self, root: Path | str = "data/ai_assistant") -> None:
        self.repo = AIAssistantRepository(root)
        self.path = self.repo.root / "conversations.json"

    def create(self, *, owner_user_id: str, provider: str, model: str, message: dict[str, Any]) -> dict[str, Any]:
        conversation = {
            "conversation_id": f"conv_{uuid4().hex[:12]}",
            "owner_user_id": owner_user_id,
            "created_at": _now(),
            "updated_at": _now(),
            "provider": provider,
            "model": model,
            "messages": [sanitize_value(message)],
            "referenced_evidence_bundle_ids": list(message.get("evidence_bundle_ids") or []),
            "referenced_entities": list(message.get("entities") or []),
            "token_usage": message.get("token_usage") or {},
            "summary": str(message.get("assistant") or "")[:600],
        }
        self._save(conversation)
        return conversation

    def append(self, conversation_id: str, *, owner_user_id: str, message: dict[str, Any]) -> dict[str, Any]:
        conversation = self.get(conversation_id, owner_user_id=owner_user_id)
        conversation["messages"].append(sanitize_value(message))
        conversation["updated_at"] = _now()
        conversation["summary"] = str(message.get("assistant") or conversation.get("summary") or "")[:600]
        conversation["referenced_evidence_bundle_ids"] = sorted(
            set(conversation.get("referenced_evidence_bundle_ids", []) + list(message.get("evidence_bundle_ids") or []))
        )
        conversation["token_usage"] = message.get("token_usage") or conversation.get("token_usage") or {}
        self._save(conversation)
        return conversation

    def list(self, *, owner_user_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        data = self._read()
        rows = data.values() if is_admin else [row for row in data.values() if row.get("owner_user_id") == owner_user_id]
        return sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)

    def get(self, conversation_id: str, *, owner_user_id: str, is_admin: bool = False) -> dict[str, Any]:
        conversation_id = validate_identifier(conversation_id)
        row = self._read().get(conversation_id)
        if not row:
            raise LookupError("conversation not found")
        if not is_admin and row.get("owner_user_id") != owner_user_id:
            raise PermissionError("conversation access denied")
        return row

    def delete(self, conversation_id: str, *, owner_user_id: str, is_admin: bool = False) -> dict[str, Any]:
        conversation_id = validate_identifier(conversation_id)
        data = self._read()
        row = data.get(conversation_id)
        if not row:
            raise LookupError("conversation not found")
        if not is_admin and row.get("owner_user_id") != owner_user_id:
            raise PermissionError("conversation access denied")
        data.pop(conversation_id)
        self.repo._write(self.path, data)
        return {"deleted": True, "conversation_id": conversation_id}

    def _read(self) -> dict[str, Any]:
        data = self.repo._read(self.path, {})
        if not isinstance(data, dict):
            raise SafeAPIError(500, "CORRUPT_AI_STORE", "AI store is unavailable")
        return data

    def _save(self, conversation: dict[str, Any]) -> None:
        data = self._read()
        raw = json.dumps(conversation, sort_keys=True, default=str, separators=(",", ":"))
        conversation["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        data[conversation["conversation_id"]] = conversation
        self.repo._write(self.path, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


conversation_store = ConversationStore()

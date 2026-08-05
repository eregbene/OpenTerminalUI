from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from backend.ai_assistant.repository import AIAssistantRepository
from backend.ai_secrets.base import SecretMetadata


class EncryptedFileSecretStore:
    """Development-only encrypted-at-rest adapter using a local XOR envelope.

    Production deployments should use a managed cloud secret manager. This adapter
    intentionally exposes only presence and metadata unless a local encryption key
    is configured through `AI_SECRET_FILE_KEY`.
    """

    source = "encrypted-file-dev"

    def __init__(self, root: Path | str = "data/ai_assistant") -> None:
        self.repo = AIAssistantRepository(root)
        self.path = self.repo.root / "provider_secrets.json"
        self.key = os.getenv("AI_SECRET_FILE_KEY", "")

    def get_secret(self, provider: str, key: str) -> str | None:
        if not self.key:
            return None
        row = self._rows().get(_row_key(provider, key))
        if not isinstance(row, dict) or not isinstance(row.get("ciphertext"), str):
            return None
        try:
            return _xor(base64.b64decode(row["ciphertext"]).decode("utf-8"), self.key)
        except Exception:
            return None

    def metadata(self, provider: str, key: str) -> SecretMetadata:
        row = self._rows().get(_row_key(provider, key))
        return SecretMetadata(provider=provider, key=key, present=bool(row), source=self.source, rotated_at=(row or {}).get("rotated_at"), version=(row or {}).get("version"))

    def _rows(self) -> dict:
        return self.repo._read(self.path, {})


def _row_key(provider: str, key: str) -> str:
    return f"{provider.lower()}:{key.lower()}"


def _xor(value: str, key: str) -> str:
    return "".join(chr(ord(ch) ^ ord(key[index % len(key)])) for index, ch in enumerate(value))

from __future__ import annotations

from backend.ai_secrets.base import SecretMetadata, SecretStore
from backend.ai_secrets.encrypted_file import EncryptedFileSecretStore
from backend.ai_secrets.environment import EnvironmentSecretStore


class SecretRegistry:
    def __init__(self) -> None:
        self._stores: tuple[SecretStore, ...] = (EnvironmentSecretStore(), EncryptedFileSecretStore())

    def get_secret(self, provider: str, key: str) -> str | None:
        for store in self._stores:
            value = store.get_secret(provider, key)
            if value:
                return value
        return None

    def metadata(self, provider: str, key: str) -> SecretMetadata:
        metas = [store.metadata(provider, key) for store in self._stores]
        present = next((meta for meta in metas if meta.present), None)
        return present or metas[0]

    def redacted_status(self, provider: str, key: str = "api_key") -> dict:
        meta = self.metadata(provider, key)
        return {"provider": provider, "key": key, "present": meta.present, "source": meta.source, "rotated_at": meta.rotated_at, "version": meta.version}


secret_registry = SecretRegistry()

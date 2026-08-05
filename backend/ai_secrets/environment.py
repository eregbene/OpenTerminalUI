from __future__ import annotations

import os

from backend.ai_secrets.base import SecretMetadata


class EnvironmentSecretStore:
    source = "environment"

    def get_secret(self, provider: str, key: str) -> str | None:
        value = os.getenv(_env_name(provider, key))
        return value if value else None

    def metadata(self, provider: str, key: str) -> SecretMetadata:
        return SecretMetadata(provider=provider, key=key, present=bool(self.get_secret(provider, key)), source=self.source, version=os.getenv(f"{_env_name(provider, key)}_VERSION"))


def _env_name(provider: str, key: str) -> str:
    if provider.lower() == "openai" and key == "api_key":
        return "OPENAI_API_KEY"
    return f"AI_SECRET_{provider.upper()}_{key.upper()}"

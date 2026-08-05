from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SecretMetadata:
    provider: str
    key: str
    present: bool
    source: str
    rotated_at: str | None = None
    version: str | None = None


class SecretStore(Protocol):
    def get_secret(self, provider: str, key: str) -> str | None:
        ...

    def metadata(self, provider: str, key: str) -> SecretMetadata:
        ...

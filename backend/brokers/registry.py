from __future__ import annotations

from typing import Any

from backend.brokers.errors import BrokerError


class BrokerRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Any] = {}

    def register(self, name: str, adapter: Any) -> None:
        self._adapters[name] = adapter

    def get(self, name: str):
        adapter = self._adapters.get(name)
        if not adapter:
            raise BrokerError("BROKER_NOT_FOUND", "broker not found", status_code=404)
        return adapter

    def list(self) -> list[Any]:
        return list(self._adapters.values())


broker_registry = BrokerRegistry()

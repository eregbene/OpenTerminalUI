from __future__ import annotations

from pathlib import Path

from backend.trading.serialization import read_json, write_json
from backend.brokers.errors import BrokerSafetyError


class IdempotencyStore:
    def __init__(self, path: Path | str = "data/brokers/idempotency.json") -> None:
        self.path = Path(path)

    def reserve(self, key: str, payload_hash: str) -> None:
        data = read_json(self.path)
        existing = data.get(key)
        if existing and existing.get("payload_hash") != payload_hash:
            raise BrokerSafetyError("DUPLICATE_IDEMPOTENCY_KEY", "idempotency key already reserved", status_code=409)
        data[key] = {"payload_hash": payload_hash, "status": "reserved"}
        write_json(self.path, data)

    def finalize(self, key: str, broker_order_id: str) -> None:
        data = read_json(self.path)
        row = data.get(key, {})
        row.update({"status": "finalized", "broker_order_id": broker_order_id})
        data[key] = row
        write_json(self.path, data)


idempotency_store = IdempotencyStore()

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ResearchRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"experiments": {}, "backtests": {}, "optimizations": {}, "validations": {}, "scorecards": {}, "candidates": {}, "artifacts": {}, "events": {}}, indent=2), encoding="utf-8")

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        data = self.load()
        data.setdefault(bucket, {})[key] = payload
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")

    def get(self, bucket: str, key: str) -> dict[str, Any] | None:
        return self.load().get(bucket, {}).get(key)

    def list(self, bucket: str) -> list[dict[str, Any]]:
        return list(self.load().get(bucket, {}).values())

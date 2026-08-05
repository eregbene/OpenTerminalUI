from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def model_to_jsonable(value: BaseModel | dict[str, Any] | list[Any]) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [model_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: model_to_jsonable(item) for key, item in value.items()}
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

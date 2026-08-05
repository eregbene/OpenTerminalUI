from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.research.models import ArtifactReference, stable_hash, stable_id, utc_now


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, artifact_type: str, payload: Any, metadata: dict[str, Any] | None = None) -> ArtifactReference:
        raw = json.dumps(payload, sort_keys=True, default=str, indent=2)
        content_hash = stable_hash(raw)
        artifact_id = stable_id("artifact", artifact_type, content_hash, utc_now())
        path = self.root / f"{artifact_id}.json"
        path.write_text(raw, encoding="utf-8")
        return ArtifactReference(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=str(path),
            content_hash=content_hash,
            metadata=metadata or {},
        )

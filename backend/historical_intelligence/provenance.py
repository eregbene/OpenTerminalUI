"""Durable, provenance-tagged persistence for Historical Intelligence research results.

Wraps the existing backend/research/{registry,artifacts}.py machinery rather than
introducing a new persistence mechanism. That machinery defaults to repo-relative
paths ("data/research/...") which resolve under /app inside the backend container --
the same unmounted, container-writable-layer location this module exists to get
results OFF of. _RESEARCH_ROOT below is therefore deliberately an absolute path
into the volume-mounted /data directory, not ResearchConfig()'s default. Do not
"simplify" this back to ResearchConfig() defaults -- that would silently reintroduce
the durability gap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.experiments.service import get_git_revision_hash
from backend.research.artifacts import ArtifactStore
from backend.research.models import utc_now
from backend.research.registry import ResearchRegistry

_RESEARCH_ROOT = Path("/data/research")


def preserve_research_result(
    *,
    bucket: str,
    run_id: str,
    result_type: str,
    payload: Any,
    summary: dict[str, Any],
    metadata: dict[str, Any],
    git_revision: str | None = None,
) -> dict[str, Any]:
    """Persist a research result durably with provenance.

    `payload` (potentially large/raw) is written as a content-hashed artifact under
    /data/research/artifacts/. A small registry record -- run_id, created_at, git
    revision, the artifact reference, `summary`, and `metadata` -- is upserted into
    /data/research/research_store.json[bucket][run_id] and returned. The registry
    record is small enough to also copy into git (data/research/research_store.json
    is tracked; data/research/artifacts/ is gitignored), matching the existing
    convention for that file.

    `git_revision`: pass this explicitly when calling from inside a container where
    `git rev-parse HEAD` can't see the repo's .git directory (the Dockerfile does not
    copy it) -- get_git_revision_hash() would otherwise silently return "dirty".
    Omit it when running on a host checkout where git is genuinely available.
    """
    registry = ResearchRegistry(_RESEARCH_ROOT / "research_store.json")
    artifacts = ArtifactStore(_RESEARCH_ROOT / "artifacts")

    ref = artifacts.write_json(result_type, payload, metadata=metadata)

    entry: dict[str, Any] = {
        "run_id": run_id,
        "result_type": result_type,
        "created_at": utc_now().isoformat(),
        "git_revision": git_revision or get_git_revision_hash(),
        "artifact": ref.model_dump(mode="json"),
        "summary": summary,
        "metadata": metadata,
    }
    registry.save(bucket, run_id, entry)
    return entry


def load_research_result(*, bucket: str, run_id: str) -> dict[str, Any] | None:
    """Read back a registry entry saved via preserve_research_result (small record only;
    does not load the underlying artifact payload)."""
    registry = ResearchRegistry(_RESEARCH_ROOT / "research_store.json")
    return registry.get(bucket, run_id)

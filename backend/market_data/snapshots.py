from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class DatasetSnapshot(BaseModel):
    snapshot_id: str
    instruments: list[str]
    asset_classes: list[str]
    timeframes: list[str]
    start: datetime
    end: datetime
    provider: str
    adjustment_mode: str
    validation_policy: str
    quality_status: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dataset_version: str = "1"
    content_hash: str
    resampling_config: dict[str, Any] = Field(default_factory=dict)
    excluded_records: list[dict[str, Any]] = Field(default_factory=list)
    corrected_records: list[dict[str, Any]] = Field(default_factory=list)


def create_snapshot_metadata(
    *,
    instruments: list[str],
    asset_classes: list[str],
    timeframes: list[str],
    start: datetime,
    end: datetime,
    provider: str,
    adjustment_mode: str,
    validation_policy: str,
    rows: list[dict[str, Any]],
    resampling_config: dict[str, Any] | None = None,
) -> DatasetSnapshot:
    payload = {
        "instruments": instruments,
        "asset_classes": asset_classes,
        "timeframes": timeframes,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "provider": provider,
        "adjustment_mode": adjustment_mode,
        "validation_policy": validation_policy,
        "rows": rows,
        "resampling_config": resampling_config or {},
    }
    content = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return DatasetSnapshot(
        snapshot_id=f"snap_{digest[:16]}",
        instruments=instruments,
        asset_classes=asset_classes,
        timeframes=timeframes,
        start=start,
        end=end,
        provider=provider,
        adjustment_mode=adjustment_mode,
        validation_policy=validation_policy,
        quality_status="validated",
        content_hash=digest,
        resampling_config=resampling_config or {},
    )

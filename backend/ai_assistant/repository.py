from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.ai_assistant.security import (
    AUDIT_RETENTION_DAYS,
    BUNDLE_RETENTION_DAYS,
    CLEANUP_BATCH_SIZE,
    MAX_AUDIT_BYTES,
    MAX_BUNDLE_BYTES,
    MAX_STORAGE_BYTES,
    SafeAPIError,
    bounded_json_bytes,
    sanitize_value,
    validate_identifier,
)


class AIAssistantRepository:
    def __init__(self, root: Path | str = "data/ai_assistant") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._verify_under_root(self.root)
        self.bundles_path = self.root / "evidence_bundles.json"
        self.audit_path = self.root / "audit.json"
        self._lock = threading.RLock()

    def _read(self, path: Path, default: Any) -> Any:
        path = self._safe_path(path)
        if not path.exists():
            self._write(path, default)
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SafeAPIError(500, "CORRUPT_AI_STORE", "AI store is unavailable") from exc

    def _write(self, path: Path, payload: Any) -> None:
        path = self._safe_path(path)
        size = bounded_json_bytes(payload)
        limit = MAX_AUDIT_BYTES if path.name == "audit.json" else MAX_BUNDLE_BYTES
        if size > limit:
            raise SafeAPIError(413, "PAYLOAD_TOO_LARGE", "AI payload too large")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def save_bundle(self, bundle: dict[str, Any]) -> None:
        validate_identifier(bundle["bundle_id"], kind="bundle_id")
        bundle = sanitize_value(bundle)
        with self._lock:
            self._enforce_storage_limit()
            data = self._read(self.bundles_path, {})
            data[bundle["bundle_id"]] = bundle
            self._write(self.bundles_path, data)

    def get_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        bundle_id = validate_identifier(bundle_id, kind="bundle_id")
        with self._lock:
            bundle = self._read(self.bundles_path, {}).get(bundle_id)
        return bundle if isinstance(bundle, dict) else None

    def append_audit(self, record: dict[str, Any]) -> None:
        record = sanitize_value(record)
        raw = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
        record["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._lock:
            data = self._read(self.audit_path, [])
            data.append(record)
            self._write(self.audit_path, data)

    def cleanup(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        removed_bundles = 0
        removed_audits = 0
        with self._lock:
            bundles = self._read(self.bundles_path, {})
            kept_bundles = {}
            for key, bundle in bundles.items():
                expires_at = _parse_dt(bundle.get("expires_at"))
                created_at = _parse_dt(bundle.get("created_at"))
                expired = expires_at is not None and expires_at <= now
                aged = created_at is not None and created_at < now - timedelta(days=BUNDLE_RETENTION_DAYS)
                if (expired or aged) and removed_bundles < CLEANUP_BATCH_SIZE:
                    removed_bundles += 1
                    continue
                kept_bundles[key] = bundle
            self._write(self.bundles_path, kept_bundles)

            audits = self._read(self.audit_path, [])
            kept_audits = []
            for record in audits:
                ts = _parse_dt(record.get("timestamp"))
                if ts is not None and ts < now - timedelta(days=AUDIT_RETENTION_DAYS) and removed_audits < CLEANUP_BATCH_SIZE:
                    removed_audits += 1
                    continue
                kept_audits.append(record)
            self._write(self.audit_path, kept_audits)
        return {"removed_bundles": removed_bundles, "removed_audits": removed_audits}

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        self._verify_under_root(resolved)
        if resolved.is_symlink():
            raise SafeAPIError(500, "AI_STORE_UNSAFE", "AI store is unavailable")
        return resolved

    def _verify_under_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            if path != self.root:
                raise SafeAPIError(500, "AI_STORE_UNSAFE", "AI store is unavailable") from exc

    def _enforce_storage_limit(self) -> None:
        total = 0
        for path in self.root.glob("*.json"):
            safe = self._safe_path(path)
            total += safe.stat().st_size if safe.exists() else 0
        if total > MAX_STORAGE_BYTES:
            raise SafeAPIError(507, "AI_STORE_FULL", "AI store limit reached")


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


repository = AIAssistantRepository()

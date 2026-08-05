from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.economic_intelligence.config import EconomicIntelligenceConfig

logger = logging.getLogger(__name__)

_USER_AGENT = "BensimTrading-EconomicIntelligence/1.0 (+demo-mt5-research)"
_REQUIRED_ROW_FIELDS = ("title", "country", "date", "impact")


class ForexFactorySchemaError(Exception):
    """Raised when the weekly JSON payload doesn't match the expected shape."""


async def fetch_weekly_calendar(config: EconomicIntelligenceConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and normalize the Forex Factory weekly JSON calendar export.

    Returns (rows, meta) where meta = {"status": "ok"|"timeout"|"schema_changed"|"unavailable", "reason": ...}.
    Never raises -- a Forex Factory failure must not crash the trading cycle.
    """
    start = datetime.now(timezone.utc)
    last_exc: Exception | None = None
    for attempt in range(max(1, config.ff_max_retries)):
        try:
            async with httpx.AsyncClient(timeout=config.ff_request_timeout_seconds, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}) as client:
                response = await client.get(config.ff_weekly_json_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and not response.text.strip().startswith("["):
                raise ForexFactorySchemaError(f"unexpected_content_type:{content_type}")
            payload = response.json()
            rows = _normalize_payload(payload)
            latency_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            return rows, {"status": "ok", "reason": None, "latency_ms": latency_ms, "records_received": len(rows)}
        except ForexFactorySchemaError as exc:
            logger.warning("Forex Factory calendar schema changed: %s", exc)
            return [], {"status": "schema_changed", "reason": str(exc)}
        except httpx.TimeoutException as exc:
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response is not None and exc.response.status_code in {403, 429}:
                break
        except Exception as exc:  # noqa: BLE001 - provider isolation boundary
            last_exc = exc
        if attempt < config.ff_max_retries - 1:
            await asyncio.sleep(min(2**attempt, 10))
    reason = f"{last_exc.__class__.__name__}:{last_exc}" if last_exc else "unknown_failure"
    logger.warning("Forex Factory calendar fetch failed after retries: %s", reason)
    return [], {"status": "unavailable" if not isinstance(last_exc, httpx.TimeoutException) else "timeout", "reason": reason}


def _normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ForexFactorySchemaError("top_level_payload_not_a_list")
    rows: list[dict[str, Any]] = []
    for raw_row in payload:
        if not isinstance(raw_row, dict):
            continue
        missing = [field for field in _REQUIRED_ROW_FIELDS if field not in raw_row]
        if missing:
            # A single malformed row is a data-quality issue, not a schema break -- skip it.
            continue
        rows.append(
            {
                "provider_event_id": _event_id(raw_row),
                "raw_name": str(raw_row.get("title") or "Unknown event"),
                "currency": str(raw_row.get("country") or "").upper(),
                "impact": _impact(raw_row.get("impact")),
                "scheduled_at": raw_row.get("date"),
                "source_timezone": "forex_factory_feed",
                "actual_raw": raw_row.get("actual"),
                "forecast_raw": raw_row.get("forecast"),
                "previous_raw": raw_row.get("previous"),
                "detail_url": raw_row.get("url"),
                "raw": raw_row,
            }
        )
    return rows


def _event_id(row: dict[str, Any]) -> str:
    existing = row.get("id") or row.get("eventId")
    if existing:
        return str(existing)
    basis = f"{row.get('title')}|{row.get('country')}|{row.get('date')}"
    import hashlib

    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _impact(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"high", "red"}:
        return "high"
    if text in {"medium", "moderate", "orange", "yellow"}:
        return "medium"
    if text in {"low", "yellow-low", "grey", "gray"}:
        return "low"
    if text in {"holiday", "none"}:
        return "none"
    return "unknown"

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.brokers.mt5.adapter import mt5_adapter
from backend.brokers.mt5.exceptions import MT5UnavailableError
from backend.decision_context.config import DecisionContextConfig, decision_context_config
from backend.decision_context.persistence import (
    build_snapshot,
    historical_context,
    persist_events,
    persist_macro,
    persist_news,
    provider_health,
    query_events,
    save_provider_state,
)
from backend.decision_context.providers import alpha_vantage_query, fred_observations, gdelt_query


class DecisionContextService:
    def __init__(self, config: DecisionContextConfig | None = None) -> None:
        self.config = config or decision_context_config()
        self._active_jobs: set[str] = set()
        self._lock = asyncio.Lock()

    async def provider_health(self) -> dict[str, Any]:
        return {"items": provider_health(), "keys_exposed": False}

    async def forex_context(self, symbol: str, timestamp: datetime | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(build_snapshot, symbol, at=timestamp)

    async def context_risk(self, symbol: str, timestamp: datetime | None = None) -> dict[str, Any]:
        snapshot = await self.forex_context(symbol, timestamp)
        block_reasons = list(snapshot["block_reasons"])
        warnings = list(snapshot["warnings"])
        if self.config.strict_mode and snapshot.get("mt5_calendar_freshness") in {"unavailable", "stale", "authentication_failed"}:
            reason = f"CONTEXT_CALENDAR_{str(snapshot.get('mt5_calendar_freshness')).upper()}"
            if reason not in block_reasons:
                block_reasons.append(reason)
            snapshot["combined_context_risk_state"] = "blocked_context_unavailable" if "UNAVAILABLE" in reason else "blocked_context_stale"
        elif snapshot.get("mt5_calendar_freshness") not in {"fresh"}:
            warnings.append(f"calendar_{snapshot.get('mt5_calendar_freshness')}")
        return {
            "symbol": snapshot["symbol"],
            "context_id": snapshot["context_id"],
            "allowed": not block_reasons,
            "allowed_with_warning": bool(warnings) and not block_reasons,
            "acknowledgement_required": bool(snapshot["acknowledgement_requirements"]) or (bool(warnings) and not block_reasons),
            "state": snapshot["combined_context_risk_state"],
            "scheduled_event_risk": snapshot["scheduled_event_risk"],
            "headline_risk": snapshot["headline_risk"],
            "warnings": warnings,
            "block_reasons": block_reasons,
            "evidence": snapshot["evidence"],
        }

    async def calendar(self, *, symbol: str | None = None, currency: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"items": await asyncio.to_thread(query_events, symbol, currency, max(1, min(500, limit)))}

    async def historical(self, symbol: str, timestamp: datetime) -> dict[str, Any]:
        return await asyncio.to_thread(historical_context, symbol, timestamp)

    async def refresh(self, jobs: list[str] | None = None) -> dict[str, Any]:
        requested = jobs or ["mt5_calendar_incremental_refresh", "gdelt_news_refresh", "alpha_vantage_news_refresh", "fred_macro_refresh", "news_deduplication", "forex_context_rebuild"]
        results: dict[str, Any] = {}
        for job in requested:
            results[job] = await self._run_once(job)
        return {"status": "complete", "results": results}

    async def _run_once(self, job_type: str) -> dict[str, Any]:
        async with self._lock:
            if job_type in self._active_jobs:
                return {"status": "duplicate_active_job_rejected"}
            self._active_jobs.add(job_type)
        try:
            start = time.monotonic()
            if job_type in {"mt5_calendar_initial_sync", "mt5_calendar_incremental_refresh"}:
                result = await self._refresh_mt5_calendar(job_type)
            elif job_type == "gdelt_news_refresh":
                result = await self._refresh_gdelt()
            elif job_type == "alpha_vantage_news_refresh":
                result = await self._refresh_alpha_vantage()
            elif job_type == "fred_macro_refresh":
                result = await self._refresh_fred()
            elif job_type in {"news_deduplication", "forex_context_rebuild"}:
                result = {"status": "ok", "records_received": 0, "records_created": 0, "records_updated": 0, "duplicates": 0}
            else:
                result = {"status": "unsupported_job"}
            result["duration_ms"] = int((time.monotonic() - start) * 1000)
            return result
        finally:
            async with self._lock:
                self._active_jobs.discard(job_type)

    async def _enabled_symbols(self) -> list[str]:
        universe = await mt5_adapter.forex_universe()
        return [row.canonical_pair for row in universe.items if row.eligible]

    async def _refresh_mt5_calendar(self, job_type: str) -> dict[str, Any]:
        start = datetime.now(timezone.utc) - timedelta(days=self.config.mt5_calendar_history_days if job_type == "mt5_calendar_initial_sync" else 1)
        end = datetime.now(timezone.utc) + timedelta(days=self.config.mt5_calendar_lookahead_days)
        try:
            status = await mt5_adapter.calendar_status()
            if not status.get("available"):
                raise MT5UnavailableError("MT5 calendar functions unavailable; install/attach the MQL5 calendar bridge or use a MetaTrader5 package build exposing calendar_value_history")
            rows = await mt5_adapter.calendar_values(start, end)
            symbols = await self._enabled_symbols()
            events = [_normalize_mt5_event(row) for row in rows]
            counts = persist_events(events, symbols)
            save_provider_state("mt5_calendar", job_type, status="ok", records_requested=len(rows), records_received=len(events), records_created=counts["created"], records_updated=counts["updated"], duplicates=counts["duplicates"], errors=[], last_successful_sync=datetime.now(timezone.utc), next_scheduled_sync=datetime.now(timezone.utc) + timedelta(seconds=self.config.mt5_calendar_refresh_seconds))
            return {"status": "ok", "records_received": len(events), **counts, "access_method": "MetaTrader5 Python calendar_value_history via existing bridge"}
        except Exception as exc:
            save_provider_state("mt5_calendar", job_type, status="unavailable", errors=[exc.__class__.__name__], next_scheduled_sync=datetime.now(timezone.utc) + timedelta(seconds=self.config.mt5_calendar_refresh_seconds))
            return {"status": "unavailable", "error": f"{exc.__class__.__name__}:{exc}", "access_method": "blocked"}

    async def _refresh_gdelt(self) -> dict[str, Any]:
        try:
            items = await asyncio.to_thread(gdelt_query, self.config)
            symbols = await self._enabled_symbols()
            counts = persist_news(items, symbols)
            save_provider_state("gdelt", "gdelt_news_refresh", status="ok", records_requested=self.config.gdelt_max_results_per_query, records_received=len(items), records_created=counts["created"], records_updated=counts["updated"], duplicates=counts["duplicates"], errors=[], last_successful_sync=datetime.now(timezone.utc), next_scheduled_sync=datetime.now(timezone.utc) + timedelta(seconds=self.config.gdelt_refresh_interval_seconds))
            return {"status": "ok", "records_received": len(items), **counts}
        except Exception as exc:
            save_provider_state("gdelt", "gdelt_news_refresh", status="unavailable", errors=[exc.__class__.__name__])
            return {"status": "unavailable", "error": exc.__class__.__name__}

    async def _refresh_alpha_vantage(self) -> dict[str, Any]:
        try:
            items, meta = await asyncio.to_thread(alpha_vantage_query, self.config)
            symbols = await self._enabled_symbols()
            counts = persist_news(items, symbols)
            status = meta.get("status") or "ok"
            save_provider_state("alpha_vantage", "alpha_vantage_news_refresh", status=status, records_requested=50, records_received=len(items), records_created=counts["created"], records_updated=counts["updated"], duplicates=counts["duplicates"], errors=[] if status == "ok" else [meta.get("reason")], last_successful_sync=datetime.now(timezone.utc) if status == "ok" else None, next_scheduled_sync=datetime.now(timezone.utc) + timedelta(seconds=self.config.alpha_vantage_refresh_interval_seconds))
            return {"status": status, "records_received": len(items), **counts, "api_key_exposed": False, "reason": meta.get("reason")}
        except Exception as exc:
            save_provider_state("alpha_vantage", "alpha_vantage_news_refresh", status="unavailable", errors=[exc.__class__.__name__])
            return {"status": "unavailable", "error": exc.__class__.__name__, "api_key_exposed": False}

    async def _refresh_fred(self) -> dict[str, Any]:
        try:
            symbols = await self._enabled_symbols()
            currencies = {symbol[:3] for symbol in symbols} | {symbol[3:6] for symbol in symbols}
            rows, meta = await asyncio.to_thread(fred_observations, self.config, currencies)
            counts = persist_macro(rows)
            status = meta.get("status") or "ok"
            save_provider_state("fred", "fred_macro_refresh", status=status, records_requested=len(currencies), records_received=len(rows), records_created=counts["created"], records_updated=counts["updated"], duplicates=counts["duplicates"], errors=[] if status == "ok" else [meta.get("reason")], last_successful_sync=datetime.now(timezone.utc) if status == "ok" else None, next_scheduled_sync=datetime.now(timezone.utc) + timedelta(seconds=self.config.fred_refresh_interval_seconds))
            return {"status": status, "records_received": len(rows), **counts, "api_key_exposed": False, "reason": meta.get("reason")}
        except Exception as exc:
            save_provider_state("fred", "fred_macro_refresh", status="unavailable", errors=[exc.__class__.__name__])
            return {"status": "unavailable", "error": exc.__class__.__name__, "api_key_exposed": False}


def _normalize_mt5_event(row: dict[str, Any]) -> dict[str, Any]:
    scheduled = _event_time(row)
    currency = str(row.get("currency") or row.get("currency_code") or "").upper()
    return {
        "provider_event_id": row.get("event_id") or row.get("id") or row.get("event_code"),
        "provider_value_id": row.get("value_id") or row.get("id"),
        "calendar_change_id": row.get("change_id"),
        "event_name": row.get("event_name") or row.get("name") or row.get("event"),
        "event_code": row.get("event_code"),
        "country": row.get("country"),
        "country_code": row.get("country_code"),
        "currency": currency,
        "event_category": row.get("sector") or row.get("category") or row.get("type"),
        "importance": _importance(row.get("importance") or row.get("priority")),
        "scheduled_at_source": scheduled,
        "source_timezone": row.get("source_timezone") or "mt5_trade_server",
        "scheduled_at_utc": scheduled,
        "period": row.get("period"),
        "actual_value": _mt5_value(row.get("actual_value") or row.get("actual")),
        "forecast_value": _mt5_value(row.get("forecast_value") or row.get("forecast")),
        "previous_value": _mt5_value(row.get("previous_value") or row.get("prev_value") or row.get("previous")),
        "revised_previous_value": _mt5_value(row.get("revised_previous_value") or row.get("revised")),
        "value_unit": row.get("unit"),
        "multiplier": row.get("multiplier"),
        "status": row.get("status") or ("released" if row.get("actual_value") is not None or row.get("actual") is not None else "scheduled"),
        "raw_payload": row,
    }


def _event_time(row: dict[str, Any]) -> datetime:
    value = row.get("time") or row.get("scheduled_at_utc") or row.get("scheduled_at_source")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _importance(value: Any) -> str:
    raw = str(value or "").lower()
    if raw in {"3", "high", "important"}:
        return "high"
    if raw in {"2", "medium", "moderate"}:
        return "medium"
    if raw in {"1", "low"}:
        return "low"
    return "unknown"


def _mt5_value(value: Any) -> float | None:
    try:
        if value in {None, "", "."}:
            return None
        number = float(value)
        if abs(number) >= 9_000_000_000_000:
            return None
        return number
    except Exception:
        return None


decision_context_service = DecisionContextService()

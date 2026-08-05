from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.economic_intelligence import calendar_guard, macro_context, news_guard, provider_health
from backend.economic_intelligence.config import EconomicIntelligenceConfig, economic_intelligence_config
from backend.economic_intelligence.event_mapping import normalize_broker_symbol
from backend.economic_intelligence.persistence import (
    is_definition_stale,
    query_events,
    query_news,
    record_provider_run,
    save_trade_context_snapshot,
    upsert_definition,
    upsert_event,
    upsert_news_item,
)
from backend.economic_intelligence.providers.forex_factory_calendar import fetch_weekly_calendar
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import backfill_range
from backend.economic_intelligence.providers.forex_factory_event_detail import fetch_event_detail
from backend.economic_intelligence.providers.forex_factory_news import fetch_news

logger = logging.getLogger(__name__)

JOB_CALENDAR = "ff_calendar_refresh"
JOB_NEWS = "ff_news_refresh"
JOB_EVENT_DETAIL = "ff_event_detail_refresh"
ALL_JOBS = (JOB_CALENDAR, JOB_NEWS, JOB_EVENT_DETAIL)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _acquire_lock(job: str, owner: str, ttl: int) -> bool:
    """Redis SETNX lock so only one worker performs a given scheduled job when multiple
    backend workers are running. Fails open (assume sole owner) if Redis is unreachable --
    mirrors backend/services/redis_quote_bus.py's acquire_aggregator_lock pattern."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return True
    try:
        from redis import asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True)
        try:
            return bool(await client.set(f"lock:economic_intelligence:{job}", owner, ex=ttl, nx=True))
        finally:
            await client.close()
    except Exception:
        return True


class EconomicIntelligenceService:
    def __init__(self, config: EconomicIntelligenceConfig | None = None) -> None:
        self.config = config or economic_intelligence_config()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._owner = f"ei:{socket.gethostname()}:{os.getpid()}"
        self._active_jobs: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._scheduler_loop(), name="economic-intelligence-scheduler")
        logger.warning("Economic intelligence scheduler started owner=%s", self._owner)

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.warning("Economic intelligence scheduler stopped")

    async def _scheduler_loop(self) -> None:
        next_due = {JOB_CALENDAR: utcnow(), JOB_NEWS: utcnow(), JOB_EVENT_DETAIL: utcnow() + timedelta(minutes=5)}
        tick_seconds = 15
        while not self._stop_event.is_set():
            now = utcnow()
            try:
                if self.config.ff_calendar_enabled and now >= next_due[JOB_CALENDAR]:
                    await self._run_once(JOB_CALENDAR)
                    next_due[JOB_CALENDAR] = now + timedelta(seconds=self._calendar_interval(now))
                if self.config.ff_news_enabled and now >= next_due[JOB_NEWS]:
                    await self._run_once(JOB_NEWS)
                    next_due[JOB_NEWS] = now + timedelta(seconds=self.config.ff_news_refresh_seconds)
                if self.config.ff_event_detail_enabled and now >= next_due[JOB_EVENT_DETAIL]:
                    await self._run_once(JOB_EVENT_DETAIL)
                    next_due[JOB_EVENT_DETAIL] = now + timedelta(hours=6)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Economic intelligence scheduler tick failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass

    def _calendar_interval(self, now: datetime) -> int:
        upcoming = query_events(start=now, end=now + timedelta(minutes=self.config.ff_release_poll_window_minutes), limit=20)
        if any(str(row.get("impact")) == "high" for row in upcoming):
            return self.config.ff_release_poll_seconds
        return self.config.ff_calendar_refresh_seconds

    async def refresh(self, jobs: list[str] | None = None) -> dict[str, Any]:
        requested = jobs or list(ALL_JOBS)
        results: dict[str, Any] = {}
        for job in requested:
            results[job] = await self._run_once(job)
        return {"status": "complete", "results": results}

    async def _run_once(self, job: str) -> dict[str, Any]:
        async with self._lock:
            if job in self._active_jobs:
                return {"status": "duplicate_active_job_rejected"}
            self._active_jobs.add(job)
        try:
            if not await _acquire_lock(job, self._owner, ttl=max(30, self.config.ff_calendar_refresh_seconds)):
                return {"status": "duplicate_active_job_rejected_other_worker"}
            started = utcnow()
            try:
                if job == JOB_CALENDAR:
                    result = await self._refresh_calendar()
                elif job == JOB_NEWS:
                    result = await self._refresh_news()
                elif job == JOB_EVENT_DETAIL:
                    result = await self._refresh_stale_event_details()
                else:
                    result = {"status": "unsupported_job"}
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a Forex Factory failure must not crash the trading cycle
                logger.warning("Economic intelligence job %s failed: %s", job, exc.__class__.__name__)
                result = {"status": "unavailable", "error": exc.__class__.__name__}
            finished = utcnow()
            provider_name = {JOB_CALENDAR: "ff_calendar_json", JOB_NEWS: "ff_news", JOB_EVENT_DETAIL: "ff_event_detail"}[job]
            record_provider_run(provider_name, started_at=started, finished_at=finished, status=str(result.get("status") or "unknown"), records_received=int(result.get("records_received") or 0), records_inserted=int(result.get("created") or 0), records_updated=int(result.get("updated") or 0), latency_ms=result.get("latency_ms"), failure_reason=result.get("error") or result.get("reason"))
            return result
        finally:
            async with self._lock:
                self._active_jobs.discard(job)

    async def _refresh_calendar(self) -> dict[str, Any]:
        rows, meta = await fetch_weekly_calendar(self.config)
        if meta.get("status") != "ok":
            provider_health.record_failure("ff_calendar_json", reason=str(meta.get("reason")), schema_changed=meta.get("status") == "schema_changed")
            return {"status": meta.get("status"), "reason": meta.get("reason"), "records_received": 0}
        created = updated = 0
        for row in rows:
            outcome = upsert_event(row, provider="ff_calendar_json")
            if outcome.get("created"):
                created += 1
            elif outcome.get("revised_fields"):
                updated += 1
        provider_health.record_success("ff_calendar_json", records_received=len(rows))
        await provider_health.cache_last_known_good("ff_calendar_json", rows, ttl_seconds=self.config.ff_calendar_degraded_after_seconds)
        return {"status": "ok", "records_received": len(rows), "created": created, "updated": updated, "latency_ms": meta.get("latency_ms")}

    async def _refresh_news(self) -> dict[str, Any]:
        rows, meta = await fetch_news(self.config)
        if meta.get("status") != "ok":
            provider_health.record_failure("ff_news", reason=str(meta.get("reason")), schema_changed=meta.get("status") == "schema_changed")
            return {"status": meta.get("status"), "reason": meta.get("reason"), "records_received": 0}
        created = 0
        for row in rows:
            outcome = upsert_news_item(row)
            if outcome.get("created"):
                created += 1
        provider_health.record_success("ff_news", records_received=len(rows))
        return {"status": "ok", "records_received": len(rows), "created": created, "latency_ms": meta.get("latency_ms")}

    async def _refresh_stale_event_details(self) -> dict[str, Any]:
        events = query_events(limit=50)
        refreshed = 0
        for event in events:
            name = str(event.get("normalized_name") or "")
            if not name or not is_definition_stale(name, stale_after_days=self.config.ff_event_detail_stale_days):
                continue
            detail_url = event.get("detail_url")
            if not detail_url:
                continue
            definition, meta = await fetch_event_detail(detail_url, self.config)
            if meta.get("status") == "ok" and definition:
                definition["normalized_name"] = name
                upsert_definition(definition)
                refreshed += 1
        provider_health.record_success("ff_event_detail", records_received=refreshed)
        return {"status": "ok", "records_received": refreshed, "created": refreshed}

    async def backfill(self, start_month: str, end_month: str, *, resume_from: str | None = None) -> dict[str, Any]:
        result = await backfill_range(start_month, end_month, self.config, resume_from=resume_from)
        total_created = 0
        for month_result in result.get("results", {}).values():
            for row in month_result[0] if isinstance(month_result, tuple) else []:
                outcome = upsert_event(row, provider="ff_calendar_scraper")
                total_created += 1 if outcome.get("created") else 0
        return result | {"events_upserted": total_created}

    async def provider_health_report(self) -> dict[str, Any]:
        return provider_health.health_snapshot(self.config)

    async def evaluate_entry(self, *, canonical_pair: str, direction: str | None = None, candidate_id: str | None = None, spread: float | None = None) -> dict[str, Any]:
        return await self._evaluate(symbol=canonical_pair, direction=direction, position_open=False, cycle_id=candidate_id, spread=spread)

    async def evaluate_position(self, *, symbol: str, direction: str | None = None, opened_at: datetime | None = None) -> dict[str, Any]:
        return await self._evaluate(symbol=symbol, direction=direction, position_open=True, cycle_id=None, spread=None)

    async def context_for_symbol(self, symbol: str) -> dict[str, Any]:
        return await self._evaluate(symbol=symbol, direction=None, position_open=False, cycle_id=None, spread=None)

    async def _evaluate(self, *, symbol: str, direction: str | None, position_open: bool, cycle_id: str | None, spread: float | None) -> dict[str, Any]:
        now = utcnow()
        parts = normalize_broker_symbol(symbol)
        health = provider_health.health_snapshot(self.config)
        calendar_state = next((row for row in health["items"] if row["provider"] == "ff_calendar_json"), {"state": "UNAVAILABLE"})
        events = query_events(currencies=list(parts.currencies), start=now - timedelta(hours=1), end=now + timedelta(hours=6), limit=50)
        calendar_result = calendar_guard.evaluate(list(parts.currencies), now, events, self.config)
        if calendar_state["state"] == provider_health.UNAVAILABLE and not provider_health.entry_allowed_for_state(provider_health.UNAVAILABLE, self.config) and not position_open:
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result("BLOCK", ["FF_CALENDAR_PROVIDER_UNAVAILABLE"]))
        elif calendar_state["state"] in {provider_health.STALE, provider_health.DEGRADED}:
            downgrade = "REDUCE_SIZE" if calendar_state["state"] == provider_health.STALE else "MANAGE_EXISTING_ONLY"
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result(downgrade, [f"FF_CALENDAR_{calendar_state['state']}"]))
        news_items = query_news(currencies=list(parts.currencies), max_age_minutes=self.config.ff_news_max_age_minutes, limit=20)
        advisory: dict[str, Any] | None = None
        if self.config.ff_openai_macro_classification_enabled and (events or news_items) and cycle_id:
            context = macro_context.build_context(
                candidate={"canonical_pair": parts.canonical_symbol, "direction": direction},
                calendar_result=calendar_result,
                news_result=calendar_guard.empty_result(),
                nearby_events=events,
                nearby_news=news_items,
                spread=spread,
                volatility=None,
                exposure=None,
                open_positions=None,
            )
            advisory = await macro_context.classify(context, idempotency_key=f"macro:{cycle_id}", config=self.config)
        pseudo_classification = {"risk_level": advisory["risk_level"], "confidence": advisory["confidence"], "urgency": "high" if advisory["risk_level"] in {"high", "critical"} else ""} if advisory else None
        news_result = news_guard.evaluate(pseudo_classification, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=position_open, config=self.config)
        combined = macro_context.combine_with_advisory(calendar_result, news_result, advisory=advisory)
        snapshot_id = save_trade_context_snapshot(
            {
                "trading_cycle_id": cycle_id,
                "symbol": parts.canonical_symbol,
                "direction": direction,
                "calendar_context": calendar_result,
                "news_context": news_result,
                "openai_context": advisory or {},
                "deterministic_decision": combined["decision"],
                "reason_codes": combined["reason_codes"],
            }
        )
        return {
            "snapshot_id": snapshot_id,
            "guard": combined,
            "calendar": calendar_result,
            "news": news_result,
            "macro_advisory": advisory,
            "nearby_events": events[:10],
            "nearby_news": news_items[:10],
            "provider_state": calendar_state["state"],
        }


economic_intelligence_service = EconomicIntelligenceService()

from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.persistence import query_trades
from backend.economic_intelligence import calendar_guard, macro_context, news_guard, provider_health
from backend.economic_intelligence.config import EconomicIntelligenceConfig, economic_intelligence_config
from backend.economic_intelligence.event_mapping import normalize_broker_symbol, severity_label_for_event
from backend.economic_intelligence.persistence import (
    event_revisions,
    event_snapshots,
    get_event,
    get_trade_context_snapshot,
    is_definition_stale,
    link_snapshot_execution,
    log_definition_cache_event,
    query_events,
    query_news,
    query_shadow_decisions,
    record_order_outcome,
    record_provider_run,
    save_trade_context_snapshot,
    unlinked_shadow_decisions,
    upsert_definition,
    upsert_event,
    upsert_news_item,
)
from backend.portfolio_execution.orm import ExecutionOrderORM
from backend.portfolio_execution.service import portfolio_manager
from backend.shared.db import SessionLocal
from backend.economic_intelligence.providers.forex_factory_calendar import fetch_weekly_calendar
from backend.economic_intelligence.providers.forex_factory_calendar_scraper import backfill_range
from backend.economic_intelligence.providers.forex_factory_event_detail import EVENT_DETAIL_SELECTOR_VERSION, fetch_event_detail_by_name
from backend.economic_intelligence.providers.forex_factory_news import fetch_news

logger = logging.getLogger(__name__)

JOB_CALENDAR = "ff_calendar_refresh"
JOB_NEWS = "ff_news_refresh"
JOB_EVENT_DETAIL = "ff_event_detail_refresh"
JOB_OUTCOME_LINK = "ff_shadow_outcome_link"
ALL_JOBS = (JOB_CALENDAR, JOB_NEWS, JOB_EVENT_DETAIL)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


_WINDOW_MINUTES_BY_SEVERITY = {
    "CENTRAL_BANK_POLICY_CRITICAL": ("ff_central_bank_block_before_minutes", "ff_central_bank_delay_after_minutes"),
    "MACRO_DATA_HIGH": ("ff_high_impact_block_before_minutes", "ff_high_impact_delay_after_minutes"),
    "MACRO_DATA_MEDIUM": ("ff_medium_impact_block_before_minutes", "ff_medium_impact_delay_after_minutes"),
}


def _log_calendar_block(calendar_result: dict[str, Any], *, currencies: tuple[str, ...], config: EconomicIntelligenceConfig) -> None:
    """Structured log line whenever the calendar guard restricts an entry/position -- makes it
    obvious WHY a given event received the protection window it did (see the classification
    refinement in event_mapping.is_central_bank_event / severity_label_for_event). Example:
    'FOMC Rate Decision' -> CENTRAL_BANK_POLICY_CRITICAL -> 60m pre / 30m post, versus
    'Cleveland Fed Inflation Expectations' -> MACRO_DATA_LOW -> no hard block.
    """
    if calendar_result.get("decision") == "ALLOW":
        return
    event = calendar_result.get("nearest_event")
    if not event:
        logger.warning(
            "Economic guard %s (currencies=%s, reasons=%s) -- no single pinned calendar event (provider/spread condition, not a specific scheduled release)",
            calendar_result.get("decision"), currencies, calendar_result.get("reason_codes"),
        )
        return
    severity_label, reason = severity_label_for_event(event)
    scheduled_at = event.get("scheduled_at_utc")
    scheduled_dt = _parse_dt(scheduled_at)
    window_fields = _WINDOW_MINUTES_BY_SEVERITY.get(severity_label)
    block_start = block_end = None
    if scheduled_dt is not None and window_fields is not None:
        before_field, after_field = window_fields
        block_start = (scheduled_dt - timedelta(minutes=getattr(config, before_field))).isoformat()
        block_end = (scheduled_dt + timedelta(minutes=getattr(config, after_field))).isoformat()
    logger.warning(
        "Economic guard %s: event=%r source=%s category=%s provider_importance=%s severity=%s currencies=%s "
        "event_time=%s block_start=%s block_end=%s classification_reason=%s rule=%s",
        calendar_result.get("decision"),
        event.get("raw_name") or event.get("normalized_name"),
        event.get("provider"),
        "central_bank_policy" if event.get("is_central_bank_event") else "scheduled_economic_release",
        event.get("impact"),
        severity_label,
        currencies,
        scheduled_at,
        block_start,
        block_end,
        reason,
        (calendar_result.get("reason_codes") or [None])[0],
    )


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
        self._next_due: dict[str, datetime] = {}
        self._fast_poll_active = False

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
        self._next_due = {JOB_CALENDAR: utcnow(), JOB_NEWS: utcnow(), JOB_EVENT_DETAIL: utcnow() + timedelta(minutes=5), JOB_OUTCOME_LINK: utcnow() + timedelta(minutes=10)}
        tick_seconds = 15
        while not self._stop_event.is_set():
            now = utcnow()
            try:
                if self.config.ff_calendar_enabled and now >= self._next_due[JOB_CALENDAR]:
                    await self._run_once(JOB_CALENDAR)
                    self._next_due[JOB_CALENDAR] = now + timedelta(seconds=self._calendar_interval(now))
                if self.config.ff_news_enabled and now >= self._next_due[JOB_NEWS]:
                    await self._run_once(JOB_NEWS)
                    self._next_due[JOB_NEWS] = now + timedelta(seconds=self.config.ff_news_refresh_seconds)
                if self.config.ff_event_detail_enabled and now >= self._next_due[JOB_EVENT_DETAIL]:
                    await self._run_once(JOB_EVENT_DETAIL)
                    self._next_due[JOB_EVENT_DETAIL] = now + timedelta(hours=6)
                if self.config.ff_shadow_outcome_link_enabled and now >= self._next_due[JOB_OUTCOME_LINK]:
                    try:
                        await self.link_shadow_outcomes()
                    except Exception as exc:
                        logger.warning("Economic intelligence outcome-link tick failed: %s", exc.__class__.__name__)
                    self._next_due[JOB_OUTCOME_LINK] = now + timedelta(minutes=10)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Economic intelligence scheduler tick failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass

    def _calendar_interval(self, now: datetime) -> int:
        """JSON-only fast polling around a release window -- the HTML scraper/event-detail
        provider are never invoked from this path, so fast polling never launches a new
        Playwright browser. Widened to cover both the pre-release lead time and the
        post-release confirmation window (FF_RELEASE_FAST_POLL_BEFORE/AFTER_MINUTES)."""
        if not self.config.ff_release_fast_poll_enabled:
            return self.config.ff_calendar_refresh_seconds
        before = self.config.ff_release_fast_poll_before_minutes
        after = self.config.ff_release_fast_poll_after_minutes
        upcoming = query_events(start=now - timedelta(minutes=after), end=now + timedelta(minutes=max(before, self.config.ff_release_poll_window_minutes)), limit=20)
        imminent = [row for row in upcoming if str(row.get("impact")) in {"high", "medium"} or row.get("is_central_bank_event")]
        active = bool(imminent)
        if active and not self._fast_poll_active:
            logger.info("Economic intelligence release fast-polling started: %d imminent event(s)", len(imminent))
        elif not active and self._fast_poll_active:
            logger.info("Economic intelligence release fast-polling stopped")
        self._fast_poll_active = active
        if active:
            return self.config.ff_release_fast_poll_seconds or self.config.ff_release_poll_seconds
        return self.config.ff_calendar_refresh_seconds

    async def event_timeline(self, event_id: str) -> dict[str, Any]:
        """Read-only: ordered snapshots + revisions for one event, from tables already
        written by upsert_event() -- no new persistence."""
        event = get_event(event_id)
        if not event:
            return {"status": "not_found", "event_id": event_id}
        return {
            "status": "ok",
            "event": event,
            "snapshots": event_snapshots(event_id),
            "revisions": event_revisions(event_id),
        }

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
        selector_version = meta.get("selector_version")
        if meta.get("status") == "schema_changed":
            provider_health.record_failure("ff_news", reason=str(meta.get("reason")), schema_changed=True)
            logger.warning("ff_news selector sanity failure, preserving last-known-good: reason=%s selector_version=%s", meta.get("reason"), selector_version)
            return {"status": "schema_changed", "reason": meta.get("reason"), "records_received": 0, "selector_version": selector_version}
        if meta.get("status") != "ok" and meta.get("status") != "degraded":
            provider_health.record_failure("ff_news", reason=str(meta.get("reason")), schema_changed=False)
            return {"status": meta.get("status"), "reason": meta.get("reason"), "records_received": 0, "selector_version": selector_version}
        created = 0
        for row in rows:
            outcome = upsert_news_item(row)
            if outcome.get("created"):
                created += 1
        if meta.get("status") == "degraded":
            provider_health.record_degraded("ff_news", reason=str(meta.get("reason")), records_received=len(rows), selector_version=selector_version)
        else:
            provider_health.record_success("ff_news", records_received=len(rows), selector_version=selector_version)
        if rows:
            await provider_health.cache_last_known_good("ff_news", rows, ttl_seconds=self.config.ff_calendar_degraded_after_seconds)
        return {"status": meta.get("status"), "records_received": len(rows), "created": created, "latency_ms": meta.get("latency_ms"), "selector_version": selector_version}

    async def _refresh_stale_event_details(self) -> dict[str, Any]:
        events = query_events(limit=50)
        refreshed = failed = hits = misses = 0
        for event in events:
            name = str(event.get("normalized_name") or "")
            if not name:
                continue
            if not is_definition_stale(name, stale_after_days=self.config.ff_event_detail_stale_days):
                hits += 1
                provider_health.record_cache_event("ff_event_detail", hit=True)
                log_definition_cache_event(name, "hit")
                continue
            misses += 1
            provider_health.record_cache_event("ff_event_detail", hit=False)
            log_definition_cache_event(name, "miss")
            outcome = await self.refresh_event_detail(name, scheduled_at=_parse_dt(event.get("scheduled_at_utc")))
            if outcome.get("status") in {"ok", "partial"}:
                refreshed += 1
            else:
                failed += 1
        if refreshed or not failed:
            provider_health.record_success("ff_event_detail", records_received=refreshed, selector_version=EVENT_DETAIL_SELECTOR_VERSION)
        return {"status": "ok", "records_received": refreshed, "created": refreshed, "cache_hits": hits, "cache_misses": misses, "failed": failed}

    async def refresh_event_detail(self, normalized_name: str, *, scheduled_at: datetime | None = None) -> dict[str, Any]:
        """Fetch and persist one event's definition on demand -- used by both the stale-refresh
        sweep and the manual POST /events/{id}/refresh-detail route. A failed scrape here never
        raises and never affects trade execution (event details are enrichment only)."""
        definition, meta = await fetch_event_detail_by_name(normalized_name, scheduled_at, self.config)
        status = meta.get("status")
        if definition:
            definition["normalized_name"] = normalized_name
            upsert_definition(definition)
            log_definition_cache_event(normalized_name, "refreshed", reason=meta.get("reason"))
        else:
            log_definition_cache_event(normalized_name, "failed", reason=meta.get("reason"))
            provider_health.record_failure("ff_event_detail", reason=str(meta.get("reason")), schema_changed=status == "schema_changed")
        return {"status": status, "definition": definition, "meta": meta}

    async def refresh_event_detail_for_event_id(self, event_id: str) -> dict[str, Any]:
        """Manual on-demand trigger for POST /events/{event_id}/refresh-detail -- looks up the
        stored event, then reuses the exact same refresh_event_detail() path as the automatic
        stale-refresh sweep."""
        event = get_event(event_id)
        if not event:
            return {"status": "not_found", "event_id": event_id}
        name = str(event.get("normalized_name") or "")
        result = await self.refresh_event_detail(name, scheduled_at=_parse_dt(event.get("scheduled_at_utc")))
        return {"event_id": event_id, "normalized_name": name, **result}

    async def backfill(self, start_month: str, end_month: str, *, resume_from: str | None = None) -> dict[str, Any]:
        result = await backfill_range(start_month, end_month, self.config, resume_from=resume_from)
        total_created = 0
        for month_result in result.get("results", {}).values():
            for row in month_result[0] if isinstance(month_result, tuple) else []:
                outcome = upsert_event(row, provider="ff_calendar_scraper")
                total_created += 1 if outcome.get("created") else 0
        return result | {"events_upserted": total_created}

    async def provider_health_report(self) -> dict[str, Any]:
        return provider_health.health_snapshot(self.config, next_due=self._next_due)

    async def evaluate_entry(self, *, canonical_pair: str, direction: str | None = None, candidate_id: str | None = None, spread: float | None = None) -> dict[str, Any]:
        """Dashboard/manual-testing entry point (see POST /api/economic-intelligence/evaluate-
        entry) -- may use the OpenAI macro-advisory feature when
        config.ff_openai_macro_classification_enabled is set. The MT5 autonomous execution path
        must NEVER call this -- use evaluate_entry_deterministic instead, which is structurally
        LLM-free regardless of that config flag."""
        return await self._evaluate(symbol=canonical_pair, direction=direction, position_open=False, cycle_id=candidate_id, spread=spread, enable_llm_macro_advisory=True)

    async def evaluate_entry_deterministic(self, *, canonical_pair: str, direction: str | None = None, candidate_id: str | None = None, spread: float | None = None) -> dict[str, Any]:
        """THE ONLY economic-risk entry point the MT5 autonomous execution path may call.
        `enable_llm_macro_advisory=False` is hardcoded here, not read from config -- so
        ff_openai_macro_classification_enabled being True can never route an OpenAI call into
        an MT5 cycle through this method, no matter how that flag is set. Calendar-guard
        (scheduled economic events, central-bank classification) remains fully deterministic and
        active; the unscheduled-news guard degrades to neutral ALLOW here (see _evaluate's
        `enable_llm_macro_advisory` handling) because it currently has no deterministic
        classification source of its own -- fails safe/explicit rather than silently keeping an
        LLM-derived signal alive under a different code path."""
        return await self._evaluate(symbol=canonical_pair, direction=direction, position_open=False, cycle_id=candidate_id, spread=spread, enable_llm_macro_advisory=False)

    async def evaluate_position(self, *, symbol: str, direction: str | None = None, opened_at: datetime | None = None) -> dict[str, Any]:
        return await self._evaluate(symbol=symbol, direction=direction, position_open=True, cycle_id=None, spread=None, enable_llm_macro_advisory=False)

    async def context_for_symbol(self, symbol: str) -> dict[str, Any]:
        return await self._evaluate(symbol=symbol, direction=None, position_open=False, cycle_id=None, spread=None, enable_llm_macro_advisory=False)

    async def _evaluate(self, *, symbol: str, direction: str | None, position_open: bool, cycle_id: str | None, spread: float | None, enable_llm_macro_advisory: bool) -> dict[str, Any]:
        _llm_call_count_before = macro_context.classify_call_count()
        now = utcnow()
        parts = normalize_broker_symbol(symbol)
        health = provider_health.health_snapshot(self.config)
        calendar_state = next((row for row in health["items"] if row["provider"] == "ff_calendar_json"), {"state": "UNAVAILABLE"})
        events = query_events(currencies=list(parts.currencies), start=now - timedelta(hours=1), end=now + timedelta(hours=6), limit=50)
        calendar_result = calendar_guard.evaluate(list(parts.currencies), now, events, self.config)
        if calendar_state["state"] in {provider_health.UNAVAILABLE, provider_health.SCHEMA_CHANGED} and not provider_health.entry_allowed_for_state(calendar_state["state"], self.config) and not position_open:
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result("BLOCK", [f"FF_CALENDAR_PROVIDER_{calendar_state['state']}"]))
        elif calendar_state["state"] in {provider_health.STALE, provider_health.DEGRADED}:
            downgrade = "REDUCE_SIZE" if calendar_state["state"] == provider_health.STALE else "MANAGE_EXISTING_ONLY"
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result(downgrade, [f"FF_CALENDAR_{calendar_state['state']}"]))
        _log_calendar_block(calendar_result, currencies=parts.currencies, config=self.config)
        news_items = query_news(currencies=list(parts.currencies), max_age_minutes=self.config.ff_news_max_age_minutes, limit=20)
        advisory: dict[str, Any] | None = None
        if not enable_llm_macro_advisory:
            # Explicit, auditable "we deliberately did not attempt this" marker (never a silent
            # omission) -- distinct from "skipped_by_config_or_no_data" below, which means the
            # caller ALLOWED an LLM call but nothing qualified for one this time.
            macro_classification_status = "MACRO_CLASSIFICATION_UNAVAILABLE"
        elif self.config.ff_openai_macro_classification_enabled and (events or news_items) and cycle_id:
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
            macro_classification_status = "classified" if advisory else "unavailable_or_low_confidence"
        else:
            macro_classification_status = "skipped_by_config_or_no_data"
        pseudo_classification = {"risk_level": advisory["risk_level"], "confidence": advisory["confidence"], "urgency": "high" if advisory["risk_level"] in {"high", "critical"} else ""} if advisory else None
        news_result = news_guard.evaluate(pseudo_classification, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=position_open, config=self.config)
        combined = macro_context.combine_with_advisory(calendar_result, news_result, advisory=advisory)
        mode = self.config.ff_economic_guard_mode
        effective, execution_changed = calendar_guard.apply_guard_mode(combined, mode)
        if mode != "enforce" and combined["decision"] != "ALLOW":
            logger.info("Economic guard shadow/effective mismatch: mode=%s shadow_decision=%s effective_decision=%s symbol=%s", mode, combined["decision"], effective["decision"], parts.canonical_symbol)
        provider_freshness = {row["provider"]: {"state": row["state"], "freshness_seconds": row.get("freshness_seconds")} for row in health["items"]}
        snapshot_id = save_trade_context_snapshot(
            {
                "trading_cycle_id": cycle_id,
                "symbol": parts.canonical_symbol,
                "direction": direction,
                "calendar_context": calendar_result,
                "news_context": news_result,
                "openai_context": advisory or {},
                "macro_classification_status": macro_classification_status,
                "deterministic_decision": combined["decision"],
                "reason_codes": combined["reason_codes"],
                "economic_guard_mode": mode,
                "shadow_decision": combined["decision"],
                "effective_decision": effective["decision"],
                "execution_changed_by_economic": execution_changed,
                "nearest_event_id": (combined.get("nearest_event") or {}).get("id"),
                "minutes_to_event": combined.get("minutes_to_event"),
                "provider_freshness": provider_freshness,
                "spread_at_evaluation": spread,
            }
        )
        return {
            "snapshot_id": snapshot_id,
            "guard": effective,
            "shadow_guard": combined,
            "economic_guard_mode": mode,
            "calendar": calendar_result,
            "news": news_result,
            "macro_advisory": advisory,
            "macro_classification_status": macro_classification_status,
            "llm_calls_made": macro_context.classify_call_count() - _llm_call_count_before,
            "nearby_events": events[:10],
            "nearby_news": news_items[:10],
            "provider_state": calendar_state["state"],
            "provider_freshness": provider_freshness,
        }

    # -- Shadow mode: evaluate_entry()/evaluate_position()/context_for_symbol() above already
    # ARE "recording a shadow decision" -- every call persists economic_guard_mode/
    # shadow_decision/effective_decision via _evaluate()'s save_trade_context_snapshot(...).
    # The methods below are the query/aggregate/replay side of shadow mode.

    async def list_shadow_decisions(self, *, symbol: str | None = None, mode: str | None = None, start: datetime | None = None, end: datetime | None = None, limit: int = 200) -> dict[str, Any]:
        return {"items": query_shadow_decisions(symbol=symbol, mode=mode, start=start, end=end, limit=limit)}

    async def shadow_summary(self, *, symbol: str | None = None, mode: str | None = None, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        decisions = query_shadow_decisions(symbol=symbol, mode=mode, start=start, end=end, limit=5000)
        total = len(decisions)
        decision_counts = {"ALLOW": 0, "BLOCK": 0, "DELAY": 0, "REDUCE_SIZE": 0, "MANAGE_EXISTING_ONLY": 0}
        by_impact: dict[str, int] = {}
        by_symbol: dict[str, int] = {}
        provider_unavailable = 0
        openai_disagreement = 0
        executed_despite_block = 0
        skipped_by_other_rules = 0
        distances: list[float] = []
        pnl_samples: list[float] = []
        mfe_samples: list[float] = []
        mae_samples: list[float] = []
        for row in decisions:
            shadow = str(row.get("shadow_decision") or "ALLOW").upper()
            effective = str(row.get("effective_decision") or "ALLOW").upper()
            decision_counts[shadow] = decision_counts.get(shadow, 0) + 1
            symbol_key = str(row.get("symbol") or "UNKNOWN")
            by_symbol[symbol_key] = by_symbol.get(symbol_key, 0) + 1
            calendar_ctx = row.get("calendar_context_json") or {}
            impact = str((calendar_ctx.get("nearest_event") or {}).get("impact") or "none")
            by_impact[impact] = by_impact.get(impact, 0) + 1
            freshness = row.get("provider_freshness_json") or {}
            if any(str(v.get("state")) in {"UNAVAILABLE", "SCHEMA_CHANGED"} for v in freshness.values() if isinstance(v, dict)):
                provider_unavailable += 1
            advisory = row.get("openai_context_json") or {}
            recommended = str(advisory.get("recommended_action") or "").upper()
            if recommended and recommended != shadow:
                openai_disagreement += 1
            if shadow in {"BLOCK", "DELAY"} and effective == "ALLOW":
                executed_despite_block += 1
            elif shadow == "ALLOW" and not row.get("linked_execution_id"):
                skipped_by_other_rules += 1
            if row.get("minutes_to_event") is not None:
                distances.append(row["minutes_to_event"])
            outcome = row.get("order_outcome") or {}
            if outcome.get("realized_pnl") is not None:
                pnl_samples.append(outcome["realized_pnl"])
            if outcome.get("mfe") is not None:
                mfe_samples.append(outcome["mfe"])
            if outcome.get("mae") is not None:
                mae_samples.append(outcome["mae"])
        return {
            "total_evaluated_signals": total,
            "would_allow": decision_counts["ALLOW"],
            "would_block": decision_counts["BLOCK"],
            "would_delay": decision_counts["DELAY"],
            "would_reduce_size": decision_counts["REDUCE_SIZE"],
            "would_manage_existing_only": decision_counts["MANAGE_EXISTING_ONLY"],
            "executed_despite_shadow_block": executed_despite_block,
            "skipped_by_other_manager_rules": skipped_by_other_rules,
            "provider_unavailable_count": provider_unavailable,
            "openai_advisory_disagreement_count": openai_disagreement,
            "results_by_impact": by_impact,
            "results_by_symbol": by_symbol,
            "results_by_strategy": {},  # strategy_signal_id isn't populated by current call sites -- honest gap, not fabricated
            "nearest_event_distance_minutes_samples": distances[:200],
            "later_trade_pnl_samples": pnl_samples,
            "later_trade_mfe_samples": mfe_samples,
            "later_trade_mae_samples": mae_samples,
        }

    async def shadow_replay(self, snapshot_id: str) -> dict[str, Any]:
        """Read-only: re-derives a decision from an already-stored snapshot's persisted
        calendar/news/advisory results under the CURRENT ff_economic_guard_mode config --
        useful for 'what would this historical signal look like under today's mode setting'.
        Never re-queries live provider data (avoids look-ahead) and never executes anything."""
        row = get_trade_context_snapshot(snapshot_id)
        if not row:
            return {"status": "not_found", "snapshot_id": snapshot_id}
        calendar_result = row.get("calendar_context_json") or calendar_guard.empty_result()
        news_result = row.get("news_context_json") or calendar_guard.empty_result()
        advisory = row.get("openai_context_json") or None
        combined = macro_context.combine_with_advisory(calendar_result, news_result, advisory=advisory or None)
        effective, execution_changed = calendar_guard.apply_guard_mode(combined, self.config.ff_economic_guard_mode)
        return {
            "status": "ok",
            "snapshot_id": snapshot_id,
            "original_economic_guard_mode": row.get("economic_guard_mode"),
            "original_shadow_decision": row.get("shadow_decision"),
            "original_effective_decision": row.get("effective_decision"),
            "replay_economic_guard_mode": self.config.ff_economic_guard_mode,
            "replay_shadow_decision": combined["decision"],
            "replay_effective_decision": effective["decision"],
            "execution_changed_by_economic": execution_changed,
        }

    async def dry_run_evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pure evaluation harness -- structurally never touches MT5ExecutionService,
        portfolio_execution.service.submit_mt5_request, or mt5.order_send. It calls
        calendar_guard/news_guard/macro_context directly (or synthetic stand-ins when
        provided) and portfolio_manager.can_open_new_trade() (a read-only check, no
        mutation). 'order_send_reachable' below is a hypothetical answer, never an action."""
        symbol = str(payload.get("symbol") or "").upper()
        direction = payload.get("direction") or "LONG"
        decision_time = _parse_dt(payload.get("decision_time")) or utcnow()
        parts = normalize_broker_symbol(symbol)
        synthetic_provider_state = payload.get("synthetic_provider_state") or {}
        synthetic_events = payload.get("synthetic_events")
        synthetic_news = payload.get("synthetic_news")
        spread = payload.get("spread")

        if synthetic_events is not None:
            events = [_synthetic_event_row(row, decision_time) for row in synthetic_events]
            calendar_state = synthetic_provider_state.get("calendar_state") or provider_health.HEALTHY
        else:
            events = query_events(currencies=list(parts.currencies), start=decision_time - timedelta(hours=1), end=decision_time + timedelta(hours=6), limit=50)
            health = provider_health.health_snapshot(self.config)
            calendar_state = next((row["state"] for row in health["items"] if row["provider"] == "ff_calendar_json"), provider_health.UNAVAILABLE)
            if synthetic_provider_state.get("calendar_state"):
                calendar_state = synthetic_provider_state["calendar_state"]

        calendar_result = calendar_guard.evaluate(list(parts.currencies), decision_time, events, self.config)
        if calendar_state in {provider_health.UNAVAILABLE, provider_health.SCHEMA_CHANGED} and not provider_health.entry_allowed_for_state(calendar_state, self.config):
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result("BLOCK", [f"FF_CALENDAR_PROVIDER_{calendar_state}"]))
        elif calendar_state in {provider_health.STALE, provider_health.DEGRADED}:
            downgrade = "REDUCE_SIZE" if calendar_state == provider_health.STALE else "MANAGE_EXISTING_ONLY"
            calendar_result = calendar_guard.combine(calendar_result, calendar_guard.empty_result(downgrade, [f"FF_CALENDAR_{calendar_state}"]))

        if synthetic_news is not None:
            classification = {"risk_level": synthetic_news.get("risk_level", "low"), "confidence": synthetic_news.get("confidence", 0.9), "urgency": synthetic_news.get("urgency", "low")}
        else:
            classification = None
        news_result = news_guard.evaluate(classification, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=False, config=self.config)

        advisory = payload.get("synthetic_openai_advisory")
        combined = macro_context.combine_with_advisory(calendar_result, news_result, advisory=advisory)
        mode = self.config.ff_economic_guard_mode
        effective, execution_changed = calendar_guard.apply_guard_mode(combined, mode)

        portfolio_allowed, portfolio_blockers = portfolio_manager.can_open_new_trade()
        final_decision = effective["decision"] if portfolio_allowed else "BLOCK"
        final_reason_codes = sorted(set(effective["reason_codes"]) | (set(portfolio_blockers) if not portfolio_allowed else set()))

        size_multiplier = float(effective.get("size_multiplier") or 1.0)
        proposed_volume = payload.get("volume")
        adjusted_volume = round(proposed_volume * size_multiplier, 4) if proposed_volume is not None else None

        live_trading_blocked = bool(mt5_config().live_trading_enabled)
        order_send_reachable = portfolio_allowed and final_decision not in {"BLOCK", "DELAY"} and not live_trading_blocked

        economic_context_payload = {
            "guard": effective,
            "shadow_guard": combined,
            "calendar": calendar_result,
            "news": news_result,
            "macro_advisory": advisory,
            "economic_guard_mode": mode,
            "spread_at_evaluation": spread,
        }
        logger.info("Economic intelligence dry-run evaluation: symbol=%s direction=%s final_decision=%s order_send_reachable=%s", symbol, direction, final_decision, order_send_reachable)
        return {
            "symbol": parts.canonical_symbol,
            "direction": direction,
            "calendar_decision": calendar_result,
            "news_decision": news_result,
            "provider_health_decision": {"calendar_state": calendar_state},
            "openai_advisory": advisory,
            "portfolio_manager_decision": {"allowed": portfolio_allowed, "blockers": portfolio_blockers},
            "final_restrictive_decision": final_decision,
            "adjusted_volume": adjusted_volume,
            "reason_codes": final_reason_codes,
            "recheck_at": effective.get("recheck_at"),
            "economic_context_payload": economic_context_payload,
            "order_send_reachable": order_send_reachable,
            "order_sent": False,
            "live_trading_blocked": live_trading_blocked,
            "economic_guard_mode": mode,
        }

    async def link_execution(self, snapshot_id: str, execution_idempotency_key: str) -> bool:
        """Called by the entry pipeline right after building a trade intent so a later
        outcome-linking pass can find its way from an economic snapshot to the eventual
        journaled order (backend/brokers/mt5/autonomous.py::_submit)."""
        return link_snapshot_execution(snapshot_id, execution_idempotency_key)

    async def link_shadow_outcomes(self) -> dict[str, Any]:
        """Backfills realized PnL/duration/exit-reason onto already-linked shadow snapshots
        once the corresponding MT5 trade has actually closed (via the same closed-trade
        history backend.brokers.mt5.persistence.query_trades already maintains -- no PnL
        logic reimplemented here). Blocked/delayed shadow decisions never have a
        linked_execution_id in the first place (no real order was attempted), so they are
        never given a fabricated outcome."""
        if not self.config.ff_shadow_outcome_link_enabled:
            return {"status": "disabled", "linked": 0}
        pending = unlinked_shadow_decisions(limit=100)
        linked = 0
        for snapshot in pending:
            execution_key = snapshot.get("linked_execution_id")
            if not execution_key:
                continue
            with SessionLocal() as db:
                order = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == execution_key).first()
                order_ticket = str(order.order_ticket) if order and order.order_ticket else None
            if not order_ticket:
                continue
            trades = query_trades(limit=10, symbol=snapshot.get("symbol"))
            match = next((t for t in trades if str(t.get("order_ticket") or "") == order_ticket and t.get("close_timestamp")), None)
            if not match:
                continue  # not yet closed -- never fabricate a result
            outcome = {
                "kind": "executed_result",
                "realized_pnl": match.get("realized_pnl"),
                "duration_seconds": match.get("duration_seconds"),
                "exit_reason": match.get("exit_reason"),
                "hit_stop_loss": str(match.get("exit_reason") or "").upper() in {"STOP_LOSS", "SL"},
                "hit_take_profit": str(match.get("exit_reason") or "").upper() in {"TAKE_PROFIT", "TP"},
                "closed_at": match.get("close_timestamp"),
            }
            if record_order_outcome(snapshot["id"], outcome):
                linked += 1
        return {"status": "ok", "linked": linked, "candidates": len(pending)}

    async def research_price_reaction(self, event_id: str, symbol: str) -> dict[str, Any]:
        """Optional research comparison: hypothetical post-signal price movement from the
        already-built historical_impact service, explicitly tagged as simulated -- never
        confused with a real executed result. Reuses the same point-in-time-correct
        (latest_snapshot_as_of) discipline as everywhere else in this module."""
        event = get_event(event_id)
        if not event:
            return {"status": "not_found", "event_id": event_id}
        from backend.brokers.mt5.adapter import mt5_adapter
        from backend.economic_intelligence.historical_impact import analyze_event_reaction

        result = await analyze_event_reaction(event, symbol, mt5_adapter)
        return {"kind": "simulated_research", **result}


def _synthetic_event_row(event: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw_minutes = event.get("minutes_from_now")
    minutes_from_now = float(raw_minutes) if raw_minutes is not None else 10.0
    return {
        "currency": str(event.get("currency") or "").upper(),
        "impact": event.get("impact") or "high",
        "scheduled_at_utc": (now + timedelta(minutes=minutes_from_now)).isoformat(),
        "is_central_bank_event": bool(event.get("is_central_bank_event") or False),
        "raw_name": event.get("raw_name") or "Synthetic Event",
    }


economic_intelligence_service = EconomicIntelligenceService()

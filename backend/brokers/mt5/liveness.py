"""2026-08-19: real incident -- the MT5 multi-account autonomous scheduler silently stopped
completing cycles for ~3 hours (a Docker Desktop engine hang on the host). No crash, no
exception, no error log -- it just quietly stopped making progress, and nothing surfaced this
until a user noticed no trades were happening and asked. This is the heartbeat check that
would have caught it within minutes instead of hours: it watches the SAME in-memory timestamp
the scheduler itself updates on every completed multi-account cycle
(MT5MultiAccountAutonomousOrchestrator.state.last_cycle_time), and fires a loud, unmistakable
alert the moment that timestamp goes stale.

Deliberately read-only and side-effect-free with respect to trading: this module never touches
a position, an order, or scheduler state. It only observes and alerts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class MT5LivenessMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        # Tracks the last_cycle_time value we already alerted on, so a sustained stall doesn't
        # spam a fresh alert on every poll -- one alert per stall episode. Cleared as soon as
        # cycles resume, so a FUTURE stall alerts again.
        self._alerted_for_cycle_time: datetime | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="mt5-liveness-monitor")
        logger.warning("MT5 liveness monitor started")

    async def stop(self) -> None:
        if not self._task:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.warning("MT5 liveness monitor stopped")

    async def _loop(self) -> None:
        assert self._stop_event is not None
        interval = _env_int("MT5_LIVENESS_CHECK_INTERVAL_SECONDS", 60)
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                logger.warning("MT5 liveness check failed (fails open, never blocks trading): %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def check_once(self) -> dict[str, object] | None:
        """Runs one liveness check. Returns the alert payload if a NEW stall was detected and
        alerted on this call, else None (either healthy, or an already-alerted stall still in
        progress). Public and synchronous so it's directly unit-testable without the loop."""
        from backend.brokers.mt5.autonomous import mt5_autonomous_service

        threshold_seconds = _env_int("MT5_LIVENESS_STALE_THRESHOLD_SECONDS", 900)
        last_cycle_time = mt5_autonomous_service.state.last_cycle_time
        if last_cycle_time is None:
            return None  # scheduler hasn't completed its first cycle since startup yet -- not a stall
        if last_cycle_time.tzinfo is None:
            last_cycle_time = last_cycle_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_seconds = (now - last_cycle_time).total_seconds()
        if age_seconds < threshold_seconds:
            self._alerted_for_cycle_time = None
            return None
        if self._alerted_for_cycle_time == last_cycle_time:
            return None  # already alerted for this exact stall episode
        self._alerted_for_cycle_time = last_cycle_time
        age_minutes = round(age_seconds / 60, 1)
        message = f"MT5 autonomous trading scheduler has not completed a cycle in {age_minutes} minutes (last cycle: {last_cycle_time.isoformat()}). Trading may be silently stalled."
        logger.critical("MT5_LIVENESS_ALERT: %s", message)
        self._notify_in_app(message)
        return {"age_minutes": age_minutes, "last_cycle_time": last_cycle_time.isoformat(), "message": message}

    def _notify_in_app(self, message: str) -> None:
        try:
            from backend.api.routes.notifications import create_notification

            with SessionLocal() as db:
                create_notification(
                    db=db,
                    type="system",
                    priority="critical",
                    title="MT5 trading scheduler stalled",
                    body=message,
                    action_url="/forex",
                )
        except Exception as exc:
            logger.warning("MT5 liveness in-app notification failed (alert was still logged): %s", exc.__class__.__name__)


mt5_liveness_monitor = MT5LivenessMonitor()

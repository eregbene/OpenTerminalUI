"""MT5 trade-record reconciliation monitor (2026-08-26).

Real bug found while investigating a "why does the system show zero closed trades / zero
realized P&L today" report: mt5_trade_records.close_timestamp/realized_pnl/exit_reason are only
ever written by update_trade_history() (backend/brokers/mt5/persistence.py), which is only ever
called from MT5AutonomousTradingService.reconciliation() -- and THAT method, in turn, was only
ever wired to the manual /reconciliation API route (backend/api/routes/brokers.py), never to any
periodic background job. Every other MT5 outcome-tracking mechanism in this codebase
(CandidateOutcomeResolver, AdaptiveManagerOutcomeResolver, StrategyPerformanceMonitor) runs on
its own recurring scheduler; this one silently didn't, so mt5_trade_records rows for real,
broker-confirmed-closed trades stayed permanently stuck showing close_timestamp=None and
realized_pnl=None -- even though the position had genuinely closed (broker equity/margin/deal-
history all correctly reflect it), unless a human happened to hit that API route.

This is a thin scheduler wrapping the ALREADY-CORRECT reconciliation()/update_trade_history()
code path -- no new reconciliation logic, just makes it run automatically. Same start/stop/
_loop/run_once shape as every other monitor in this codebase for consistency.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300  # matches CandidateOutcomeResolver's own cadence


class MT5TradeReconciliationMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> bool:
        if self._task and not self._task.done():
            return True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="mt5-trade-reconciliation-monitor")
        logger.warning("MT5 trade reconciliation monitor started")
        return True

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
        logger.warning("MT5 trade reconciliation monitor stopped")

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.exception("MT5 trade reconciliation cycle failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, object]:
        # Deferred import: avoids a circular import at module load time (autonomous.py imports
        # from several other mt5_strategies/adaptive_management modules; this monitor is imported
        # from main.py alongside those, same pattern as the other MT5 monitors there).
        from backend.brokers.mt5.autonomous import mt5_autonomous_service

        result = await mt5_autonomous_service.reconciliation()
        accounts = result.get("accounts") or {}
        # update_trade_history's success return has no "status" key at all (just updated_trades/
        # reviews_created/deals_seen) -- only the caught-exception path in reconciliation() adds
        # {"status": "UNAVAILABLE", "error": ...}. So "succeeded" means status is ABSENT, not a
        # specific value.
        matched = sum(1 for row in accounts.values() if isinstance(row, dict) and "status" not in (row.get("history_sync") or {}))
        logger.info("MT5 trade reconciliation: %d account(s) processed, %d history-sync succeeded, overall status=%s", len(accounts), matched, result.get("status"))
        return {"accounts_processed": len(accounts), "history_sync_succeeded": matched, "status": result.get("status")}


mt5_trade_reconciliation_monitor = MT5TradeReconciliationMonitor()

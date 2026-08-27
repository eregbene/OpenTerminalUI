"""2026-08-26: mt5_trade_records.close_timestamp/realized_pnl were only ever written by the
manual /reconciliation API route (backend/api/routes/brokers.py) -- MT5AutonomousTradingService.
reconciliation()/update_trade_history() had no recurring scheduler, unlike every other MT5
outcome-tracking mechanism (CandidateOutcomeResolver, AdaptiveManagerOutcomeResolver,
StrategyPerformanceMonitor). This just verifies the new thin scheduler (same start/stop/_loop/
run_once shape as those) actually calls the existing, correct reconciliation() path."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.brokers.mt5.trade_reconciliation_monitor import MT5TradeReconciliationMonitor


@pytest.mark.asyncio
async def test_run_once_calls_reconciliation_and_summarizes_result():
    """update_trade_history's real success return has NO "status" key at all (just
    updated_trades/reviews_created/deals_seen) -- only the caught-exception path adds one. See
    trade_reconciliation_monitor.py's own comment for why "succeeded" means status is absent."""
    monitor = MT5TradeReconciliationMonitor()
    fake_result = {
        "status": "MATCHED_OPEN",
        "accounts": {
            "demo_10k": {"status": "MATCHED_OPEN", "history_sync": {"updated_trades": 3, "reviews_created": 3, "deals_seen": 8}},
            "ftmo_demo_50k": {"status": "MATCHED_EMPTY", "history_sync": {"updated_trades": 0, "reviews_created": 0, "deals_seen": 0}},
        },
    }
    with patch("backend.brokers.mt5.autonomous.mt5_autonomous_service") as mock_service:
        mock_service.reconciliation = AsyncMock(return_value=fake_result)
        summary = await monitor.run_once()

    mock_service.reconciliation.assert_awaited_once()
    assert summary["accounts_processed"] == 2
    assert summary["history_sync_succeeded"] == 2
    assert summary["status"] == "MATCHED_OPEN"


@pytest.mark.asyncio
async def test_run_once_does_not_count_unavailable_history_sync():
    monitor = MT5TradeReconciliationMonitor()
    fake_result = {
        "status": "MATCHED_OPEN",
        "accounts": {
            "demo_10k": {"status": "MATCHED_OPEN", "history_sync": {"status": "UNAVAILABLE", "error": "BrokerError"}},
        },
    }
    with patch("backend.brokers.mt5.autonomous.mt5_autonomous_service") as mock_service:
        mock_service.reconciliation = AsyncMock(return_value=fake_result)
        summary = await monitor.run_once()

    assert summary["accounts_processed"] == 1
    assert summary["history_sync_succeeded"] == 0


@pytest.mark.asyncio
async def test_start_stop_lifecycle():
    monitor = MT5TradeReconciliationMonitor()
    with patch.object(MT5TradeReconciliationMonitor, "run_once", new=AsyncMock(return_value={})):
        started = await monitor.start()
        assert started is True
        assert monitor._task is not None and not monitor._task.done()
        await monitor.stop()
        assert monitor._task is None


@pytest.mark.asyncio
async def test_run_once_cycle_failure_does_not_crash_loop(monkeypatch):
    monitor = MT5TradeReconciliationMonitor()
    monkeypatch.setattr(MT5TradeReconciliationMonitor, "run_once", AsyncMock(side_effect=RuntimeError("boom")))
    await monitor.start()
    # _loop swallows the exception and keeps running -- give it a moment then confirm still alive.
    import asyncio
    await asyncio.sleep(0.05)
    assert monitor._task is not None and not monitor._task.done()
    await monitor.stop()

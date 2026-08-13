"""Phase 4 (Forex/MT5 roadmap) regression tests: execution quality classification.

Covers: UNTRUSTED below the minimum sample floor (never silently reported GOOD/DEGRADED/POOR),
threshold boundaries for fill-rate/slippage-ratio/latency driving POOR vs DEGRADED vs GOOD, and
account_execution_quality's real per-symbol grouping against a live SQLite-backed ExecutionOrderORM
table (units never averaged across symbols)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.portfolio_execution import execution_quality
from backend.portfolio_execution.orm import ExecutionOrderORM
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


@dataclass
class _FakeRow:
    state: str
    latency_ms: float | None = None
    slippage: float | None = None
    spread_paid: float | None = None
    rejection_reason: str | None = None


# --- classify (pure) -----------------------------------------------------------------------


def test_classify_untrusted_below_min_sample():
    assert execution_quality.classify(sample_size=5, fill_rate=1.0, avg_slippage_to_spread_ratio=0.0, p95_latency_ms=100.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.UNTRUSTED


def test_classify_good_when_all_metrics_healthy():
    assert execution_quality.classify(sample_size=50, fill_rate=0.95, avg_slippage_to_spread_ratio=0.1, p95_latency_ms=300.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.GOOD


def test_classify_poor_on_low_fill_rate():
    assert execution_quality.classify(sample_size=50, fill_rate=0.3, avg_slippage_to_spread_ratio=0.1, p95_latency_ms=300.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.POOR


def test_classify_poor_on_extreme_slippage_ratio():
    assert execution_quality.classify(sample_size=50, fill_rate=0.95, avg_slippage_to_spread_ratio=2.0, p95_latency_ms=300.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.POOR


def test_classify_poor_on_extreme_latency():
    assert execution_quality.classify(sample_size=50, fill_rate=0.95, avg_slippage_to_spread_ratio=0.1, p95_latency_ms=5000.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.POOR


def test_classify_degraded_on_moderate_fill_rate():
    assert execution_quality.classify(sample_size=50, fill_rate=0.7, avg_slippage_to_spread_ratio=0.1, p95_latency_ms=300.0, latency_poor_ms=3000, latency_degraded_ms=1200) == execution_quality.DEGRADED


# --- execution_quality_for_group (pure, no DB) --------------------------------------------


def test_group_computes_fill_rate_and_status():
    rows = [_FakeRow(state="FILLED", latency_ms=200, slippage=0.0001, spread_paid=0.0002) for _ in range(18)] + [_FakeRow(state="REJECTED", latency_ms=150, rejection_reason="SPREAD_TOO_WIDE") for _ in range(2)]
    result = execution_quality.execution_quality_for_group(rows)
    assert result["sample_size"] == 20
    assert result["fill_rate"] == 0.9
    assert result["status"] == execution_quality.GOOD
    assert result["top_rejection_reasons"] == [("SPREAD_TOO_WIDE", 2)]


def test_group_untrusted_below_min_sample():
    rows = [_FakeRow(state="FILLED", latency_ms=100) for _ in range(3)]
    result = execution_quality.execution_quality_for_group(rows)
    assert result["status"] == execution_quality.UNTRUSTED


def test_group_never_averages_slippage_across_zero_spread_rows():
    """A row with spread_paid=0 (or None) is excluded from the ratio computation -- division by
    zero must never silently produce inf/NaN in the reported ratio."""
    rows = [_FakeRow(state="FILLED", latency_ms=100, slippage=0.0005, spread_paid=0.0) for _ in range(15)]
    result = execution_quality.execution_quality_for_group(rows)
    assert result["avg_slippage_to_spread_ratio"] is None


# --- account_execution_quality: real per-symbol grouping against SQLite --------------------


def test_account_execution_quality_groups_by_symbol_independently(monkeypatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)

    with SessionLocal() as db:
        for i in range(15):
            db.add(ExecutionOrderORM(execution_id=f"E_EUR_{i}", account_id="demo_10k", idempotency_key=f"K_EUR_{i}", source="test", symbol="EURUSD", action_type="MARKET_BUY", state="FILLED", latency_ms=200.0, slippage=0.0001, spread_paid=0.0002, created_at=NOW))
        for i in range(15):
            db.add(ExecutionOrderORM(execution_id=f"E_GBP_{i}", account_id="demo_10k", idempotency_key=f"K_GBP_{i}", source="test", symbol="GBPUSD", action_type="MARKET_BUY", state="REJECTED", rejection_reason="SPREAD_TOO_WIDE", latency_ms=200.0, created_at=NOW))
        db.commit()

    result = execution_quality.account_execution_quality("demo_10k", window=100)
    assert result["sample_size"] == 30
    assert result["by_symbol"]["EURUSD"]["fill_rate"] == 1.0
    assert result["by_symbol"]["GBPUSD"]["fill_rate"] == 0.0
    assert result["by_symbol"]["GBPUSD"]["status"] == execution_quality.POOR

"""Phase 4 (Forex/MT5 roadmap): execution quality intelligence.

Read-only analytics on top of data ExecutionOrderORM ALREADY captures on every real order_send
call (see MT5ExecutionService.submit_market_order in backend/portfolio_execution/service.py):
requested vs realized fill price (slippage), spread paid, and order_send latency/fill latency.
This module adds the one genuinely missing piece -- a GOOD/DEGRADED/POOR/UNTRUSTED classification
of that already-captured data, per account (optionally scoped to one symbol) -- rather than
re-instrumenting the live order-submission path.

Known, honest limitation (documented rather than worked around): ExecutionOrderORM has no direct
foreign key to the R-multiple outcome of the resulting trade (AdaptivePositionStateORM /
HistoricalSetupOutcomeORM key by position_id, not idempotency_key/execution_id, and there is no
reliable join between the two without guessing). "Execution-adjusted expectancy" (comparing raw
signal expectancy to expectancy after realized slippage/spread cost) therefore is NOT built here --
building it on an unverified join would produce a number that LOOKS precise but may silently
mismatch trades. This is flagged as a real, open item rather than shipped as a fragile guess.

Slippage/spread are in raw price units, which aren't comparable across symbols (a EURUSD pip is
0.0001; an XAUUSD pip is 0.01) -- every classification here is computed per (account, symbol)
group so units stay internally consistent, never averaged across symbols.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

GOOD = "GOOD"
DEGRADED = "DEGRADED"
POOR = "POOR"
UNTRUSTED = "UNTRUSTED"

MIN_SAMPLE = 10
_FILLED_STATES = {"ACCEPTED", "FILLED", "PARTIALLY_FILLED", "RECONCILED"}


def _percentile(values: list[float], pct: float) -> float | None:
    return round(float(np.percentile(np.array(values, dtype=float), pct)), 4) if values else None


def classify(*, sample_size: int, fill_rate: float, avg_slippage_to_spread_ratio: float | None, p95_latency_ms: float | None, latency_poor_ms: float, latency_degraded_ms: float) -> str:
    """Conservative, evidence-only classification -- read-only diagnostic, never a live gate.
    UNTRUSTED below MIN_SAMPLE (too few fills to mean anything, never silently reported as GOOD)."""
    if sample_size < MIN_SAMPLE:
        return UNTRUSTED
    poor = fill_rate < 0.50 or (avg_slippage_to_spread_ratio is not None and avg_slippage_to_spread_ratio > 1.5) or (p95_latency_ms is not None and p95_latency_ms > latency_poor_ms)
    if poor:
        return POOR
    degraded = fill_rate < 0.85 or (avg_slippage_to_spread_ratio is not None and avg_slippage_to_spread_ratio > 0.5) or (p95_latency_ms is not None and p95_latency_ms > latency_degraded_ms)
    if degraded:
        return DEGRADED
    return GOOD


def execution_quality_for_group(rows: list[Any], *, latency_poor_ms: float = 3000.0, latency_degraded_ms: float = 1200.0) -> dict[str, Any]:
    """`rows` is a list of ExecutionOrderORM (or any object exposing the same attributes) already
    filtered to one (account_id, symbol) group by the caller -- this function never queries the
    DB itself, keeping it trivially unit-testable against plain fixtures."""
    sample_size = len(rows)
    filled = [row for row in rows if row.state in _FILLED_STATES]
    fill_rate = (len(filled) / sample_size) if sample_size else 0.0
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    slippages = [row.slippage for row in filled if row.slippage is not None]
    spreads = [row.spread_paid for row in filled if row.spread_paid is not None and row.spread_paid > 0]
    ratios = [abs(row.slippage) / row.spread_paid for row in filled if row.slippage is not None and row.spread_paid not in (None, 0)]
    rejection_reasons = Counter(row.rejection_reason for row in rows if row.rejection_reason)

    p95_latency = _percentile(latencies, 95)
    avg_ratio = round(sum(ratios) / len(ratios), 4) if ratios else None
    status = classify(sample_size=sample_size, fill_rate=fill_rate, avg_slippage_to_spread_ratio=avg_ratio, p95_latency_ms=p95_latency, latency_poor_ms=latency_poor_ms, latency_degraded_ms=latency_degraded_ms)

    return {
        "sample_size": sample_size,
        "fill_rate": round(fill_rate, 4),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "p95_latency_ms": p95_latency,
        "avg_slippage": round(sum(slippages) / len(slippages), 6) if slippages else None,
        "avg_spread_paid": round(sum(spreads) / len(spreads), 6) if spreads else None,
        "avg_slippage_to_spread_ratio": avg_ratio,
        "top_rejection_reasons": rejection_reasons.most_common(5),
        "status": status,
    }


def account_execution_quality(account_id: str, *, window: int = 200) -> dict[str, Any]:
    """Real query: the most recent `window` orders for this account, grouped by symbol. Each
    symbol group is classified independently (see module docstring on unit comparability)."""
    from backend.portfolio_execution.orm import ExecutionOrderORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        rows = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.account_id == account_id).order_by(ExecutionOrderORM.created_at.desc()).limit(window).all()

    by_symbol: dict[str, list[Any]] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)

    symbols = {symbol: execution_quality_for_group(group_rows) for symbol, group_rows in by_symbol.items()}
    overall = execution_quality_for_group(rows)
    return {"account_id": account_id, "sample_size": len(rows), "overall": overall, "by_symbol": symbols}

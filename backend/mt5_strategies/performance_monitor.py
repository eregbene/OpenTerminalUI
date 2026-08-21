"""Strategy performance monitor (2026-08-18).

A recurring background service that keeps re-evaluating how each strategy has ACTUALLY been
performing lately -- real trade-level $ P&L for strategies currently live, real shadow-tracked
outcomes for strategies currently demoted -- and writes a RECOMMENDATION when the fresh evidence
disagrees with a strategy's current activation. This is the automated, continuously-refreshing
version of the one-off manual forensic pass that found mtfai1/session_breakout/
support_resistance_bounce net-losing on 2026-08-17 (see that commit) -- same methodology, run on
a schedule instead of by hand.

CRITICAL, BY DESIGN: this module NEVER writes to .env, docker-compose.yml, or any
MT5_STRATEGY_ACTIVATION_<ID> value, and NEVER calls anything that could change what actually
executes live. It only ever upserts rows into strategy_performance_recommendations with
status=PENDING_REVIEW. Turning a recommendation into a real activation change is a SEPARATE,
manual, human-approved step -- the user explicitly asked (2026-08-18) for self-updating evidence
with a sign-off gate before anything can auto-disable a strategy, and this is that gate: nothing
here can ever reach a broker order or an activation flag on its own.

Methodology (identical to the manual audit this automates):
  - REAL_TRADES source, for a strategy whose CURRENT activation is ACTIVE_MT5: one row per closed
    SOLO position (strategy_id matches exactly -- multi-strategy-confirmed/fused trades are
    deliberately excluded so a strategy's own standalone signal quality isn't diluted by other
    strategies' contributions) from AdaptivePositionStateORM, R approximated as
    (max_achieved_r - current_giveback_r) and $P&L as that R times original_risk_money --
    position-level, one row per real closed trade, never summed across duplicated deal/session-
    resync rows (two real double-counting bugs were found and ruled out exactly this way during
    the manual audit).
  - SHADOW_TRACKING source, for a strategy whose CURRENT activation is SHADOW_MT5 or DISABLED:
    MT5CandidateEvaluationORM rows with outcome_type='SHADOW' and a resolved realized_pnl -- the
    existing shadow-tracking outcome resolver already backfills these independently, without ever
    executing anything, roughly 5 days after each shadow candidate would have closed.

Thresholds are intentionally asymmetric (harder bar to reinstate than to flag a demotion
candidate) -- see PROMOTE_EXPECTANCY_R_THRESHOLD vs DEMOTE_EXPECTANCY_R_THRESHOLD -- to damp
flip-flopping a strategy back and forth on noise near zero.
"""
from __future__ import annotations

import asyncio
import logging
import statistics as pystats
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.mt5_strategies.models import ACTIVE_MT5, DISABLED, SHADOW_MT5, STRATEGY_FAMILIES, activation_status
from backend.mt5_strategies.orm import StrategyPerformanceRecommendationORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 21600  # 6h -- a rolling 14-day $P&L window does not meaningfully change minute to minute
WINDOW_DAYS = 14
MIN_SAMPLE_FOR_RECOMMENDATION = 20
DEMOTE_EXPECTANCY_R_THRESHOLD = -0.05
PROMOTE_EXPECTANCY_R_THRESHOLD = 0.10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _all_strategy_ids() -> list[str]:
    return list(STRATEGY_FAMILIES.keys())


def _real_trade_stats(strategy_id: str, window_start: datetime) -> dict[str, Any]:
    """Solo (non-fused) closed positions for `strategy_id` since `window_start`. Position-level
    -- never deal-level or session-resync-level, both of which were proven to massively
    double-count during the manual audit this automates."""
    with SessionLocal() as db:
        rows = (
            db.query(AdaptivePositionStateORM)
            .filter(
                AdaptivePositionStateORM.strategy_id == strategy_id,
                AdaptivePositionStateORM.closed_detected_at.isnot(None),
                AdaptivePositionStateORM.closed_detected_at > window_start,
                AdaptivePositionStateORM.original_risk_money.isnot(None),
                AdaptivePositionStateORM.max_achieved_r.isnot(None),
            )
            .all()
        )
    r_values: list[float] = []
    usd_values: list[float] = []
    for row in rows:
        realized_r = float(row.max_achieved_r) - float(row.current_giveback_r or 0.0)
        r_values.append(realized_r)
        usd_values.append(realized_r * float(row.original_risk_money))
    return _summarize(r_values, usd_values)


def _real_trade_stats_by_symbol(symbol: str, window_start: datetime) -> dict[str, Any]:
    """Same real, position-level, no-double-counting methodology as _real_trade_stats, grouped
    by symbol instead of strategy_id -- added for the confidence engine's symbol_performance
    component (QuantConnect gap-analysis Priority 1: that component was previously always a
    constant neutral score because nothing ever populated mt5_trade_memory_snapshots; this reuses
    the SAME authoritative real-trade ledger the strategy performance monitor already established,
    rather than building a second, competing performance data source)."""
    with SessionLocal() as db:
        rows = (
            db.query(AdaptivePositionStateORM)
            .filter(
                AdaptivePositionStateORM.symbol == symbol.upper(),
                AdaptivePositionStateORM.closed_detected_at.isnot(None),
                AdaptivePositionStateORM.closed_detected_at > window_start,
                AdaptivePositionStateORM.original_risk_money.isnot(None),
                AdaptivePositionStateORM.max_achieved_r.isnot(None),
            )
            .all()
        )
    r_values: list[float] = []
    usd_values: list[float] = []
    for row in rows:
        realized_r = float(row.max_achieved_r) - float(row.current_giveback_r or 0.0)
        r_values.append(realized_r)
        usd_values.append(realized_r * float(row.original_risk_money))
    return _summarize(r_values, usd_values)


def _shadow_stats(strategy_id: str, window_start: datetime) -> dict[str, Any]:
    """Shadow-tracked candidate outcomes for `strategy_id` since `window_start` -- populated by
    the existing outcome resolver independently of this monitor, never executed. realized_r is
    the resolver's own risk-normalized outcome (comparable across symbols/position sizes, same
    as the REAL_TRADES source), so the decision logic stays R-based for both sources."""
    with SessionLocal() as db:
        rows = (
            db.query(MT5CandidateEvaluationORM)
            .filter(
                MT5CandidateEvaluationORM.strategy == strategy_id,
                MT5CandidateEvaluationORM.outcome_type == "SHADOW",
                MT5CandidateEvaluationORM.realized_pnl.isnot(None),
                MT5CandidateEvaluationORM.created_at > window_start,
            )
            .all()
        )
    r_values = [float(row.realized_r) for row in rows if row.realized_r is not None]
    usd_values = [float(row.realized_pnl) for row in rows]
    return _summarize(r_values, usd_values)


def _summarize(r_values: list[float], usd_values: list[float]) -> dict[str, Any]:
    n = len(usd_values)
    wins = [v for v in usd_values if v > 0]
    return {
        "sample_size": n,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "expectancy_r": round(pystats.fmean(r_values), 4) if r_values else None,
        "realized_usd": round(sum(usd_values), 2) if usd_values else None,
        "avg_realized_usd": round(pystats.fmean(usd_values), 2) if usd_values else None,
    }


def _decide(*, current: str, stats: dict[str, Any]) -> tuple[str, str]:
    """Returns (recommended_activation, reasoning). Never recommends DISABLED -- a demotion
    candidate always lands on SHADOW_MT5 first (still tracked, never executing), matching the
    same reversible-first posture used for the 2026-08-17 manual demotions. Only ever recommends
    a CHANGE when the sample clears MIN_SAMPLE_FOR_RECOMMENDATION; otherwise explicitly reports
    INSUFFICIENT_SAMPLE rather than guessing from a thin window."""
    n = stats["sample_size"]
    if n < MIN_SAMPLE_FOR_RECOMMENDATION:
        return current, f"INSUFFICIENT_SAMPLE: only {n} closed trades in the last {WINDOW_DAYS}d (need >= {MIN_SAMPLE_FOR_RECOMMENDATION}); no change recommended."

    expectancy_r = stats["expectancy_r"]
    avg_usd = stats["avg_realized_usd"] or 0.0
    # REAL_TRADES source always has expectancy_r; SHADOW_TRACKING does not (see _shadow_stats) --
    # fall back to a sign read on avg $P&L for that source only.
    negative = (expectancy_r is not None and expectancy_r < DEMOTE_EXPECTANCY_R_THRESHOLD) or (expectancy_r is None and avg_usd < 0)
    positive = (expectancy_r is not None and expectancy_r > PROMOTE_EXPECTANCY_R_THRESHOLD) or (expectancy_r is None and avg_usd > 0)

    if current == ACTIVE_MT5 and negative:
        return SHADOW_MT5, (
            f"Real trades over the last {WINDOW_DAYS}d: n={n}, expectancy_r={expectancy_r}, "
            f"realized_usd={stats['realized_usd']}, win_rate={stats['win_rate']} -- net-losing, below "
            f"the {DEMOTE_EXPECTANCY_R_THRESHOLD}R demotion threshold. Recommend SHADOW_MT5 (still "
            f"tracked, never executing) pending human review."
        )
    if current in {SHADOW_MT5, DISABLED} and positive:
        return ACTIVE_MT5, (
            f"Shadow-tracked outcomes over the last {WINDOW_DAYS}d: n={n}, realized_usd={stats['realized_usd']}, "
            f"avg_realized_usd={avg_usd}, win_rate={stats['win_rate']} -- clears the "
            f"{PROMOTE_EXPECTANCY_R_THRESHOLD}R reinstatement bar. Recommend ACTIVE_MT5 pending human review."
        )
    return current, (
        f"n={n}, expectancy_r={expectancy_r}, realized_usd={stats['realized_usd']}, win_rate={stats['win_rate']} -- "
        f"current activation ({current}) already matches recent evidence; no change recommended."
    )


def performance_memory_for_confidence(*, strategy_id: str | None = None, symbol: str | None = None, window_days: int = WINDOW_DAYS) -> dict[str, Any] | None:
    """Bridges this module's real-trade/shadow-tracking stats into the shape
    backend/brokers/mt5/confidence.py::_performance_component expects
    ({"closed_trade_count", "win_rate", "recommendation", "expectancy"}) -- the fix for
    strategy_performance/symbol_performance, which were previously always a constant neutral
    score (mt5_trade_memory_snapshots, the table they used to read from, was never written to;
    see QuantConnect gap-analysis Priority 1). Exactly one of strategy_id/symbol must be given.

    `recommendation` is derived from this module's OWN, already-established expectancy
    thresholds (DEMOTE_EXPECTANCY_R_THRESHOLD) so a confidence component never gets a second,
    inconsistent definition of "this looks like it's losing" from the one the activation-
    recommendation pipeline already uses. Returns None (never a fabricated dict) if neither
    parameter is usable, letting the caller's existing "no recorded history, neutral default"
    fallback handle it -- consistent with every other component's own missing-data posture."""
    if bool(strategy_id) == bool(symbol):
        return None
    window_start = utcnow() - timedelta(days=window_days)
    if strategy_id:
        current = activation_status(strategy_id)
        source = "REAL_TRADES" if current == ACTIVE_MT5 else "SHADOW_TRACKING"
        stats = _real_trade_stats(strategy_id, window_start) if source == "REAL_TRADES" else _shadow_stats(strategy_id, window_start)
    else:
        stats = _real_trade_stats_by_symbol(symbol, window_start)  # type: ignore[arg-type]

    if not stats["sample_size"]:
        return None
    expectancy_r = stats["expectancy_r"]
    avg_usd = stats["avg_realized_usd"] or 0.0
    negative = (expectancy_r is not None and expectancy_r < DEMOTE_EXPECTANCY_R_THRESHOLD) or (expectancy_r is None and avg_usd < 0)
    mildly_negative = (expectancy_r is not None and expectancy_r < 0) or (expectancy_r is None and avg_usd < 0)
    recommendation = "AVOID" if negative else ("REDUCE_RISK" if mildly_negative else "NEUTRAL")
    return {
        "closed_trade_count": stats["sample_size"],
        "win_rate": stats["win_rate"] or 0.0,
        "recommendation": recommendation,
        "expectancy": expectancy_r,
    }


def compute_recommendations(*, window_days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    """Pure(ish) computation -- reads only, never writes. Exposed separately from run_once() so
    it can be called on demand (e.g. from a script or a manual check) without needing the
    recurring service running."""
    window_start = utcnow() - timedelta(days=window_days)
    results = []
    for strategy_id in _all_strategy_ids():
        current = activation_status(strategy_id)
        source = "REAL_TRADES" if current == ACTIVE_MT5 else "SHADOW_TRACKING"
        stats = _real_trade_stats(strategy_id, window_start) if source == "REAL_TRADES" else _shadow_stats(strategy_id, window_start)
        recommended, reasoning = _decide(current=current, stats=stats)
        results.append({
            "strategy_id": strategy_id, "data_source": source, "window_days": window_days,
            "current_activation": current, "recommended_activation": recommended, "reasoning": reasoning, **stats,
        })
    return results


def _persist(recommendations: list[dict[str, Any]]) -> int:
    """Upserts one row per (strategy_id, day) -- see module docstring. A row already reviewed
    (status != PENDING_REVIEW) is refreshed with fresh numbers but its review decision is
    preserved, never silently reset by a later run."""
    now = utcnow()
    day = now.strftime("%Y%m%d")
    written = 0
    with SessionLocal() as db:
        for rec in recommendations:
            recommendation_id = f"SPR_{rec['strategy_id']}_{day}"
            row = db.get(StrategyPerformanceRecommendationORM, recommendation_id)
            if row is None:
                row = StrategyPerformanceRecommendationORM(recommendation_id=recommendation_id, status="PENDING_REVIEW", created_at=now)
                db.add(row)
            row.strategy_id = rec["strategy_id"]
            row.computed_at = now
            row.window_days = rec["window_days"]
            row.data_source = rec["data_source"]
            row.sample_size = rec["sample_size"]
            row.win_rate = rec["win_rate"]
            row.expectancy_r = rec["expectancy_r"]
            row.realized_usd = rec["realized_usd"]
            row.avg_realized_usd = rec["avg_realized_usd"]
            row.current_activation = rec["current_activation"]
            row.recommended_activation = rec["recommended_activation"]
            row.reasoning = rec["reasoning"]
            row.updated_at = now
            written += 1
        db.commit()
    return written


class StrategyPerformanceMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> bool:
        if self._task and not self._task.done():
            return True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="strategy-performance-monitor")
        logger.warning("Strategy performance monitor started")
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
        logger.warning("Strategy performance monitor stopped")

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.exception("Strategy performance monitor cycle failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, int]:
        recommendations = await asyncio.to_thread(compute_recommendations)
        written = await asyncio.to_thread(_persist, recommendations)
        changes = sum(1 for r in recommendations if r["recommended_activation"] != r["current_activation"])
        logger.info("Strategy performance monitor: %d strategies evaluated, %d recommend a change, %d rows written", len(recommendations), changes, written)
        return {"evaluated": len(recommendations), "recommend_change": changes, "written": written}


strategy_performance_monitor = StrategyPerformanceMonitor()

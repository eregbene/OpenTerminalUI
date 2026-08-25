"""Bensim -- Adaptive Manager V3 continuation, Part 10: formal per-strategy DEMO activation
cutover registry.

The 7 strategies below were reactivated from SHADOW_MT5 to ACTIVE_MT5 in Stage A of the "Activate
All Strategy Families in DEMO" initiative (commit d83262c). Almost all of their prior EXECUTED/
CLOSED history in mt5_candidate_evaluations predates that reactivation (from an EARLIER active
period before the 2026-08-21 Priority-4 demotion) -- pooling it with fresh post-reactivation
evidence would let stale, pre-fix history dominate both the dashboard and (more importantly) the
automatic DEMO safety circuit's (demo_safety_circuit.py) trip decision. This registry lets any
evidence-consuming code ask "since when should THIS strategy's evidence count as current," so the
safety circuit evaluates primarily on POST_ACTIVATION trades, per the user's explicit Part 10
instruction.

Strategies not listed here (mtfai1, trend_pullback, mean_reversion -- never demoted/reactivated
this cycle) have no cutover: `cutover_for()` returns None, meaning "use all history," which is
correct for them since there's no stale pre-cutover period to exclude.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Real deploy-verified timestamp: first clean live cycle observed on the container after Stage A's
# `docker compose up -d backend` recreate (commit d83262c), same value used by
# scratch_unified_strategy_dashboard.py's REACTIVATION_AT.
STRATEGY_CUTOVER_AT: dict[str, datetime] = {
    strategy_id: datetime(2026, 8, 25, 13, 20, 0, tzinfo=timezone.utc)
    for strategy_id in (
        "smc_continuation",
        "liquidity_sweep_reversal",
        "vwap_reversion",
        "support_resistance_bounce",
        "session_breakout",
        "breakout",
        "ema_trend",
    )
}


def cutover_for(strategy_id: str) -> datetime | None:
    """None means "no cutover, use full history" -- correct for any strategy never demoted/
    reactivated this cycle."""
    return STRATEGY_CUTOVER_AT.get(strategy_id)

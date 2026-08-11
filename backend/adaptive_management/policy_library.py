from __future__ import annotations

from typing import Any

# Minimum-data-requirements gate mirrored from adaptive_management/service.py's DEFAULT_MINIMUMS
# (kept as a local literal, not an import, so this module has zero dependency on service.py --
# see the module docstring below for why that matters right now).
_MINIMUM_DATA_REQUIREMENTS = {"MIN_TRADES_PER_POLICY_GLOBAL": 20, "MIN_TRADES_PER_STRATEGY_POLICY": 10}

_BASE = {
    "version": "v1",
    "eligible_symbols": [],
    "eligible_timeframes": [],
    "eligible_regimes": [],
    "minimum_data_requirements": _MINIMUM_DATA_REQUIREMENTS,
    "validation_status": "research",
    "scorecard": {},
    "evidence_artifacts": [],
    "parent_policy_id": None,
}


def _policy(policy_id: str, name: str, eligible_strategies: list[str], description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {**_BASE, "policy_id": policy_id, "name": name, "family": "strategy_archetype", "eligible_strategies": eligible_strategies, "description": description, "parameters": parameters}


# Part 7 Management Policy Library: named, strategy-archetype policy profiles, each bundling
# risk/breakeven/partials/trailing/TP-behavior/holding-time together (unlike the 15 existing
# single-mechanic research policies in service.py's default_policies(), which vary ONE dimension
# at a time for counterfactual isolation). These are what a strategy actually SELECTS in
# production, once select_policy_for_strategy() below is wired into the live pipeline.
#
# Every numeric choice here is a deliberate, named trading-style tradeoff (documented in each
# policy's own "parameters"), not a fitted/backtested value -- these are starting points for the
# archetype, meant to be tuned once enough per-strategy history exists via the probability engine
# (AdaptiveManagementService.probability_report), not invented statistics.
ARCHETYPE_POLICIES: list[dict[str, Any]] = [
    _policy(
        "smc_manager_v1",
        "SMC Manager",
        ["SMC", "BENSIM_AUTO"],
        "Smart Money Concepts: structure-anchored stop, breakeven only after a confirmed swing/order-block retest, partials at liquidity zones, target at opposing structure.",
        {"breakeven_trigger_r": 0.75, "breakeven_requires_structure_confirmation": True, "partial_stages": [{"trigger_r": 1.0, "fraction": 0.3}], "trailing": {"trigger_r": 1.5, "trail_r": 0.7, "basis": "structure"}, "tp_reward_multiple": 2.0, "max_holding_candles": 96},
    ),
    _policy(
        "ict_manager_v1",
        "ICT Manager",
        ["ICT"],
        "Inner Circle Trader: FVG/liquidity-sweep entries managed on session kill-zone timing -- fast breakeven after displacement, time-exit tied to session end rather than a fixed candle count.",
        {"breakeven_trigger_r": 0.5, "breakeven_requires_structure_confirmation": True, "partial_stages": [{"trigger_r": 0.75, "fraction": 0.25}, {"trigger_r": 1.25, "fraction": 0.25}], "trailing": {"trigger_r": 1.0, "trail_r": 0.5, "basis": "structure"}, "tp_reward_multiple": 1.8, "max_holding_candles": 48, "session_bound_exit": True},
    ),
    _policy(
        "trend_manager_v1",
        "Trend Manager",
        ["TREND"],
        "Trending-regime trades: let winners run -- late breakeven (avoid stopping out on normal pullbacks), wide ATR trailing, minimal partials, large runner target.",
        {"breakeven_trigger_r": 1.2, "breakeven_requires_structure_confirmation": False, "partial_stages": [{"trigger_r": 2.0, "fraction": 0.2}], "trailing": {"trigger_r": 1.5, "trail_r": 1.2, "basis": "atr"}, "tp_reward_multiple": 3.5, "max_holding_candles": 192},
    ),
    _policy(
        "breakout_manager_v1",
        "Breakout Manager",
        ["BREAKOUT"],
        "Momentum breakout trades: quick breakeven and aggressive early partials, since breakouts that fail tend to fail fast -- tighter trailing to protect gains rather than chase extension.",
        {"breakeven_trigger_r": 0.4, "breakeven_requires_structure_confirmation": False, "partial_stages": [{"trigger_r": 0.6, "fraction": 0.35}, {"trigger_r": 1.2, "fraction": 0.25}], "trailing": {"trigger_r": 0.8, "trail_r": 0.4, "basis": "atr"}, "tp_reward_multiple": 1.8, "max_holding_candles": 36},
    ),
    _policy(
        "mean_reversion_manager_v1",
        "Mean Reversion Manager",
        ["MEAN_REVERSION"],
        "Counter-trend trades to a defined reversion target: fixed, closer TP rather than trailing (the thesis is a specific level, not an open-ended run), tighter risk, fast time-exit if reversion stalls.",
        {"breakeven_trigger_r": 0.5, "breakeven_requires_structure_confirmation": False, "partial_stages": [{"trigger_r": 0.75, "fraction": 0.4}], "trailing": None, "tp_reward_multiple": 1.5, "max_holding_candles": 48},
    ),
    _policy(
        "scalping_manager_v1",
        "Scalping Manager",
        ["SCALPING"],
        "Very short holding time: tight breakeven, fast partial, small realistic TP, short time-exit -- the thesis invalidates quickly if it hasn't worked within a handful of candles.",
        {"breakeven_trigger_r": 0.3, "breakeven_requires_structure_confirmation": False, "partial_stages": [{"trigger_r": 0.5, "fraction": 0.5}], "trailing": {"trigger_r": 0.6, "trail_r": 0.3, "basis": "atr"}, "tp_reward_multiple": 1.2, "max_holding_candles": 12},
    ),
    _policy(
        "swing_manager_v1",
        "Swing Manager",
        ["SWING"],
        "Multi-day swing trades: patient, wide stop, late breakeven, minimal partials, largest runner target, and the longest holding-time budget of any archetype here.",
        {"breakeven_trigger_r": 1.5, "breakeven_requires_structure_confirmation": True, "partial_stages": [{"trigger_r": 2.5, "fraction": 0.25}], "trailing": {"trigger_r": 2.0, "trail_r": 1.5, "basis": "structure"}, "tp_reward_multiple": 4.0, "max_holding_candles": 480},
    ),
]

_FALLBACK_POLICY_ID = "smc_manager_v1"


def select_policy_for_strategy(strategy_id: str | None, available_policy_ids: set[str] | None = None) -> str:
    """The strategy chooses its policy (Part 7's explicit requirement): matches strategy_id
    against each archetype policy's eligible_strategies. Falls back to smc_manager_v1 (the most
    general-purpose archetype -- structure-based, moderate on every dimension) when no archetype
    claims the strategy, rather than silently picking an unrelated aggressive/conservative
    profile. `available_policy_ids`, if given, restricts the match to policies actually present
    in the database (e.g. after ensure_default_policies() has run) -- when omitted, matches
    against the full ARCHETYPE_POLICIES list."""
    normalized = str(strategy_id or "").strip().upper()
    candidates = ARCHETYPE_POLICIES if available_policy_ids is None else [row for row in ARCHETYPE_POLICIES if row["policy_id"] in available_policy_ids]
    for policy in candidates:
        if normalized in {name.upper() for name in policy["eligible_strategies"]}:
            return policy["policy_id"]
    return _FALLBACK_POLICY_ID

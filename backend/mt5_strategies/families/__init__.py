"""Canonical strategy family implementations for the MT5 multi-strategy layer -- package form
of the original mt5_strategies/families.py monolith (2026-08-20 Phase 1 restructure; 2026-08-21
Phase 2 and Phase 3 engine-wide gates added on top).

Each evaluator takes a shared, already-computed StrategyContext (Market Data -> features, see
../context.py) and returns exactly one StrategySignal. No broker I/O, no IBKR imports, no
OpenAI. `EVALUATORS` below is the exact same dict, same keys, same call signatures, as the
pre-restructure module -- every caller (backend/brokers/mt5/autonomous.py,
backend/historical_intelligence/replay.py) imports `from backend.mt5_strategies.families import
EVALUATORS, evaluate_all` today and needs zero changes.

Module layout:
  _shared.py                    -- stop/geometry/evidence helpers every evaluator reuses, plus
                           (Phase 1) the flagged breakout/trend_pullback/smc_continuation levers,
                           (Phase 2) the engine-wide ADX regime gate, session-liquidity timing
                           filter, stale-exit metadata, and spread safety buffer, and (Phase 3)
                           the structural take-profit engine (_opposing_structural_level /
                           _structural_take_profit, wrapping backend.brokers.mt5.take_profit.
                           select_take_profit -- the same function mtfai1's own scoring uses).
                           Every gate is fail-open by construction, see that file's own headers.
  breakout.py           -- Phase 1: ATR volatility buffer, retest-and-hold entry mode, HTF trend
                           gate, consolidation quality score. Phase 2: regime disable (CHOP_
                           RANGING), regime-widened ATR buffer, QUIET_COMPRESSION retest-mode
                           preference, session-timing gate, stale-exit metadata, spread gate.
                           Phase 3: inducement (IDM) precondition on the triggering break,
                           structural take-profit.
  trend_pullback.py     -- Phase 1: anti-CHoCH/MSS gate, OTE/OB/FVG confluence mode, reaction-
                           candle trigger, independent LONG/SHORT code paths. Phase 2: stale-exit
                           metadata, spread gate (not regime- or session-restricted per spec).
  smc_continuation.py   -- Phase 1: inducement (IDM) precondition, displacement-magnitude
                           evidence. Phase 2: spread gate (not regime- or session-restricted).
  ema_trend.py          -- Phase 2: extracted out of _legacy.py for its CHOP_RANGING regime
                           disable and spread gate. Phase 3: EMA stack demoted to a bias filter
                           behind its own flag (requires a with-bias BOS or EMA20 reclaim +
                           reaction candle as the actual trigger), structural take-profit.
  mean_reversion.py     -- Phase 2: extracted out of _legacy.py for its TRENDING_STRONG regime
                           disable, CHOP_RANGING strength boost, and spread gate.
  vwap_reversion.py     -- Phase 3: extracted out of _legacy.py to fix a real bug -- the
                           cumulative VWAP never reset at a session/day boundary despite its own
                           docstring's claim, drifting over whatever ~100-bar window happened to
                           be fetched. Unconditional fix, no new flag.
  support_resistance_bounce.py -- Phase 3: extracted out of _legacy.py for its new HTF non-
                           conflict gate (reuses trend_pullback.py's exact inline pattern).
  momentum.py            -- Phase 3: extracted out of _legacy.py for MT5_MOMENTUM_STRATEGY_ENABLED,
                           a code-level deprecation circuit breaker independent of the activation-
                           status system -- default False, unlike every other Phase 1-3 flag this
                           is NOT a no-op default (see that file's own docstring).
  _legacy.py             -- the remaining registered families (liquidity_sweep_reversal,
                           session_breakout, wyckoff) plus mtfai1's non-involvement here (mtfai1
                           has never been in this module -- its evaluation lives directly in
                           backend/brokers/mt5/autonomous.py and already runs through
                           backend.historical_intelligence.entry_intelligence unconditionally on
                           every candidate; see that module's own docstring). NOT modified in any
                           phase: session_breakout IS named in the Phase 2/3 session-timing and IDM
                           tables but wiring either gate in was out of scope for those deliverables'
                           requested output-file lists -- _shared.py's gate functions already key
                           correctly on strategy_id, so doing so later is additive, not a redesign.
                           See that file's own docstring and families.py's git history.
"""
from __future__ import annotations

import os
from typing import Any

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._legacy import (
    evaluate_liquidity_sweep_reversal,
    evaluate_session_breakout,
    evaluate_wyckoff,
)
from backend.mt5_strategies.families._shared import _dynamic_stop, _no_signal, _signal
from backend.mt5_strategies.families.breakout import evaluate_breakout
from backend.mt5_strategies.families.donchian_trend_follow import evaluate_donchian_trend_follow
from backend.mt5_strategies.families.ema_trend import evaluate_ema_trend
from backend.mt5_strategies.families.fx_relative_momentum import evaluate_fx_relative_momentum
from backend.mt5_strategies.families.session_liquidity_breakout import evaluate_session_liquidity_breakout
from backend.mt5_strategies.families.mean_reversion import evaluate_mean_reversion
from backend.mt5_strategies.families.bsi_v2_engine import evaluate_bsi_v2_active as evaluate_bsi
from backend.mt5_strategies.families.bsi_v3_engine import evaluate_bsi_v3_profile_gated
from backend.mt5_strategies.families.momentum import evaluate_momentum
from backend.mt5_strategies.families.smc_continuation import evaluate_smc_continuation
from backend.mt5_strategies.families.support_resistance_bounce import evaluate_support_resistance_bounce
from backend.mt5_strategies.families.trend_pullback import evaluate_trend_pullback
from backend.mt5_strategies.families.vwap_reversion import evaluate_vwap_reversion
from backend.mt5_strategies.models import StrategySignal

if os.getenv("BSI_BASELINE_V3_UPDATED_FAIZ_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
    evaluate_bsi = evaluate_bsi_v3_profile_gated

EVALUATORS: dict[str, Any] = {
    "ema_trend": evaluate_ema_trend,
    "trend_pullback": evaluate_trend_pullback,
    "breakout": evaluate_breakout,
    "mean_reversion": evaluate_mean_reversion,
    "liquidity_sweep_reversal": evaluate_liquidity_sweep_reversal,
    "smc_continuation": evaluate_smc_continuation,
    "support_resistance_bounce": evaluate_support_resistance_bounce,
    "momentum": evaluate_momentum,
    "session_breakout": evaluate_session_breakout,
    "vwap_reversion": evaluate_vwap_reversion,
    "wyckoff": evaluate_wyckoff,
    "donchian_trend_follow": evaluate_donchian_trend_follow,
    "session_liquidity_breakout": evaluate_session_liquidity_breakout,
    "fx_relative_momentum": evaluate_fx_relative_momentum,
    "bsi": evaluate_bsi,
}


def evaluate_all(ctx: StrategyContext, *, strategy_ids: list[str] | None = None) -> list[StrategySignal]:
    """Evaluates every registered non-mtfai1 strategy family that is regime-compatible for the
    current context (Part 5) -- incompatible strategies are skipped entirely, not scored as
    invalid, so they never appear in the candidate pool for this cycle.

    Also enforces the demo-only operational circuit breaker (Part 17): a tripped strategy is
    skipped (never evaluated, never contributes a candidate) until an operator resets it, and
    every produced signal is sanity-checked (geometry/finite-price/RR) so a bug can never
    reach fusion/execution even if a strategy's own reward:risk logic missed it. This has
    nothing to do with trading performance -- losing trades never trip the breaker.

    Unchanged from the pre-restructure implementation -- same dynamic imports (avoids a circular
    import between this package and mt5_strategies.models/circuit_breaker), same control flow,
    same wire format."""
    from backend.mt5_strategies.models import DISABLED, activation_status, regime_compatible
    from backend.mt5_strategies import circuit_breaker

    ids = strategy_ids if strategy_ids is not None else list(EVALUATORS.keys())
    signals: list[StrategySignal] = []
    for strategy_id in ids:
        evaluator = EVALUATORS.get(strategy_id)
        if evaluator is None or not regime_compatible(strategy_id, ctx.regime):
            continue
        if circuit_breaker.is_tripped(strategy_id):
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="CIRCUIT_BREAKER_OPEN"))
            continue
        # Part 16: DISABLED means genuinely out of the pipeline -- unlike SHADOW_MT5 (still
        # evaluated for calibration, just never executable), a DISABLED strategy is not
        # evaluated at all. This is how wyckoff (default DISABLED) stays isolated in production.
        if activation_status(strategy_id) == DISABLED:
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="STRATEGY_DISABLED"))
            continue
        try:
            signal = evaluator(ctx)
        except Exception:
            circuit_breaker.record_evaluation_error(strategy_id)
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="evaluation_error"))
            continue
        circuit_breaker.record_evaluation_success(strategy_id)
        malformed_reason = circuit_breaker.validate_signal_sanity(signal)
        if malformed_reason is not None:
            circuit_breaker.record_malformed_signal(strategy_id)
            signal = _no_signal(ctx, strategy_id=strategy_id, family=signal.strategy_family, timeframe=signal.timeframe, reason=malformed_reason)
        signals.append(signal)
    return signals


__all__ = [
    "EVALUATORS",
    "evaluate_all",
    # Private helpers re-exported ONLY because backend/tests/test_mt5_strategy_stop_construction.py
    # already imports them directly from this module path (pre-restructure) -- kept for 100%
    # backward compatibility, not part of this package's intended public surface.
    "_dynamic_stop",
    "_signal",
    "evaluate_ema_trend",
    "evaluate_trend_pullback",
    "evaluate_breakout",
    "evaluate_mean_reversion",
    "evaluate_liquidity_sweep_reversal",
    "evaluate_smc_continuation",
    "evaluate_support_resistance_bounce",
    "evaluate_momentum",
    "evaluate_session_breakout",
    "evaluate_vwap_reversion",
    "evaluate_wyckoff",
    "evaluate_bsi",
]

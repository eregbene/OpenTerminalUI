"""Canonical strategy signal contract + activation registry for the MT5 multi-strategy layer.

Reuse note: no existing model in the codebase combines strategy identity, normalized
entry/stop/target, regime, and evidence in a broker-agnostic, IBKR-free shape (the closest
candidates -- intelligence/trading's StrategyOutput and forex_strategies' candidate model --
both carry IBKR-oriented fields or assume caller-supplied direction/entry rather than
self-computed signals). StrategySignal is the smallest clean abstraction that satisfies the
spec's required field list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ACTIVE_MT5 = "ACTIVE_MT5"
SHADOW_MT5 = "SHADOW_MT5"
DISABLED = "DISABLED"
NOT_MT5_COMPATIBLE = "NOT_MT5_COMPATIBLE"
_VALID_OVERRIDES = {ACTIVE_MT5, SHADOW_MT5, DISABLED}


def normalize_strategy_id(raw: str | None) -> str:
    """Canonical lowercase strategy identity for persistence/read boundaries, so e.g. "MTFAI1"
    and "mtfai1" are always treated as the same strategy for grouping/comparison. The UNKNOWN
    sentinel is passed through unchanged (uppercase) because some call sites retry-on-UNKNOWN
    with an exact-case guard (e.g. AdaptivePositionStateORM sync) -- lowercasing it would make
    that guard stop matching and silently disable the retry."""
    if not raw:
        return "UNKNOWN"
    text = str(raw).strip()
    if not text or text.upper() == "UNKNOWN":
        return "UNKNOWN"
    return text.lower()


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_family: str
    symbol: str
    broker_symbol: str
    direction: str  # LONG | SHORT | NO_TRADE
    timeframe: str
    generated_at: datetime
    valid: bool
    raw_signal_strength: float  # 0-100, strategy's own opinion of setup quality -- NOT the final confidence score
    proposed_entry: float | None
    stop_loss: float | None
    take_profit: float | None
    reward_risk: float | None
    regime: str
    evidence: dict[str, Any] = field(default_factory=dict)
    rejection_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def invalid_signal(strategy_id: str, strategy_family: str, *, symbol: str, broker_symbol: str, timeframe: str, generated_at: datetime, regime: str, reason: str) -> StrategySignal:
    return StrategySignal(
        strategy_id=strategy_id, strategy_family=strategy_family, symbol=symbol, broker_symbol=broker_symbol,
        direction="NO_TRADE", timeframe=timeframe, generated_at=generated_at, valid=False, raw_signal_strength=0.0,
        proposed_entry=None, stop_loss=None, take_profit=None, reward_risk=None, regime=regime, rejection_reason=reason,
    )


# --- Strategy registry: family metadata + regime compatibility + activation status. ---
# `regimes`: the set of backend.adaptive_management.service.detect_regime() labels this
# strategy is considered suitable for (Part 5). An EMPTY tuple means "not regime-gated" --
# reserved for mtfai1, whose existing production behavior must not change in this task.
# `default_activation`: promoted to ACTIVE_MT5 for all 10 canonical families (Part 7 -- the
# Stage 1 SHADOW_MT5 evaluation period is complete: optimization + regression tests passed,
# see backend/tests/test_mt5_multi_strategy.py and test_mt5_multi_strategy_optimization.py).
# Still fully config-driven and instantly reversible per strategy WITHOUT a code deploy via
# MT5_STRATEGY_ACTIVATION_<ID>=SHADOW_MT5|DISABLED (Part 16), and the per-strategy circuit
# breaker (circuit_breaker.py) forces an equivalent of DISABLED automatically on operational
# malfunction regardless of this config.
STRATEGY_FAMILIES: dict[str, dict[str, Any]] = {
    "mtfai1": {
        "family": "trend_multi_timeframe", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15", "H1", "H4"), "regimes": (),
    },
    "ema_trend": {
        "family": "ema_trend", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15", "H1"), "regimes": ("trending_up", "trending_down"),
    },
    "trend_pullback": {
        "family": "trend_pullback", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15", "H1"), "regimes": ("trending_up", "trending_down"),
    },
    "breakout": {
        "family": "breakout", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15", "H1"), "regimes": ("breakout", "high_volatility", "trending_up", "trending_down"),
    },
    "mean_reversion": {
        "family": "mean_reversion", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15",), "regimes": ("ranging", "low_volatility"),
    },
    "liquidity_sweep_reversal": {
        "family": "liquidity_sweep_reversal", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15", "H1"), "regimes": ("unstable_transition", "reversal", "ranging"),
    },
    "smc_continuation": {
        "family": "smc_continuation", "default_activation": ACTIVE_MT5,
        "timeframes": ("H4", "H1", "M15"), "regimes": ("trending_up", "trending_down", "breakout"),
    },
    "support_resistance_bounce": {
        "family": "support_resistance_bounce", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15",), "regimes": ("ranging", "low_volatility"),
    },
    "momentum": {
        "family": "momentum", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15",), "regimes": ("trending_up", "trending_down", "breakout"),
    },
    "session_breakout": {
        "family": "session_breakout", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15",), "regimes": ("breakout", "high_volatility", "unstable_transition"),
    },
    "vwap_reversion": {
        "family": "vwap_reversion", "default_activation": ACTIVE_MT5,
        "timeframes": ("M15",), "regimes": ("ranging", "low_volatility"),
    },
    "wyckoff": {
        # 2026-08-17: new independent strategy family, registered here (rather than left
        # unregistered) specifically so backend.historical_intelligence.replay's canonical
        # point-in-time-safe replay pipeline can reach evaluate_wyckoff -- replay.py's whole
        # guarantee is that it NEVER reimplements a strategy, only calls evaluate_all() for
        # whatever IS registered. default_activation is deliberately DISABLED (not SHADOW_MT5
        # like the other 10 families' own Stage-1 precedent) so the LIVE running DEMO backend's
        # own per-cycle evaluate_all() never evaluates or shadow-tracks it at all -- zero live
        # footprint -- until explicit historical/OOS validation evidence is reported and
        # promotion is approved (see docs/ WYCKOFF_STRATEGY_VALIDATION report once it exists).
        # Historical validation runs reach the real evaluator anyway by setting
        # MT5_STRATEGY_ACTIVATION_WYCKOFF=SHADOW_MT5 as a process-local env var scoped to the
        # offline replay/backfill script's own process only -- never deployed to the live
        # container's .env/docker-compose. Promote to SHADOW_MT5 (live calibration, still
        # non-executing) or ACTIVE_MT5 (real DEMO order authority) only via a later, explicit
        # deploy once validation supports it.
        "family": "wyckoff", "default_activation": DISABLED,
        "timeframes": ("M15", "H1"), "regimes": ("ranging", "reversal", "unstable_transition", "low_volatility", "breakout"),
    },
    "donchian_trend_follow": {
        # 2026-08-24: new independent strategy family (docs/donchian-trend-follow-design-
        # spec.md), registered with the exact same DISABLED-by-default, zero-live-footprint
        # precedent as wyckoff above -- the live DEMO backend's own evaluate_all() never
        # evaluates or shadow-tracks this until its 3-year point-in-time-safe validation
        # reports evidence and promotion is explicitly approved. Validation runs reach the
        # real evaluator by setting MT5_STRATEGY_ACTIVATION_DONCHIAN_TREND_FOLLOW=SHADOW_MT5
        # as a process-local env var scoped to the offline replay script's own process only.
        # regimes deliberately empty -- no hard ctx.regime gate (see design spec section 4);
        # the strategy's own mandatory volatility-expansion condition already does the
        # regime-relevant gating, avoiding trend_pullback's diagnosed double-gating mistake.
        "family": "donchian_trend_follow", "default_activation": DISABLED,
        "timeframes": ("M15", "H1"), "regimes": (),
    },
}


def multi_strategy_enabled() -> bool:
    """Global kill switch (Part 16) -- MT5_MULTI_STRATEGY_ENABLED=false reverts the whole
    cycle to MTFAI1-only behavior (identical to the pre-multi-strategy pipeline), independent
    of any per-strategy activation setting. Defaults to enabled: the multi-strategy candidate
    layer has already been running in SHADOW_MT5 in production since Stage 1."""
    raw = os.getenv("MT5_MULTI_STRATEGY_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def activation_status(strategy_id: str) -> str:
    """Config override (MT5_STRATEGY_ACTIVATION_<ID>) takes precedence over the coded default,
    so demotion/promotion/disable never requires a code change (Part 16). A tripped circuit
    breaker (Part 17) always overrides both -- an operational malfunction can never be
    outranked by activation config."""
    from backend.mt5_strategies import circuit_breaker

    if circuit_breaker.is_tripped(strategy_id):
        return DISABLED
    meta = STRATEGY_FAMILIES.get(strategy_id, {})
    override = os.getenv(f"MT5_STRATEGY_ACTIVATION_{strategy_id.upper()}")
    if override in _VALID_OVERRIDES:
        return override
    return meta.get("default_activation", SHADOW_MT5)


def regime_compatible(strategy_id: str, regime: str) -> bool:
    """Part 5: do not make every strategy trade every regime. An empty `regimes` tuple (only
    mtfai1) means unrestricted, preserving its current, unchanged production behavior."""
    meta = STRATEGY_FAMILIES.get(strategy_id)
    if not meta or not meta.get("regimes"):
        return True
    return regime in meta["regimes"]


def all_strategy_ids(*, exclude: tuple[str, ...] = ("mtfai1",)) -> list[str]:
    return [sid for sid in STRATEGY_FAMILIES if sid not in exclude]

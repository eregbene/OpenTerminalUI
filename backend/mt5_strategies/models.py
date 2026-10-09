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
    "session_liquidity_breakout": {
        # 2026-08-24: new independent strategy family (docs/session-liquidity-breakout-design-
        # spec.md), same DISABLED-by-default, zero-live-footprint precedent as wyckoff/donchian_
        # trend_follow above. regimes empty -- session_breakout (the control this is benchmarked
        # against) is itself regime-gated (breakout/high_volatility/unstable_transition); this
        # new strategy is deliberately left unrestricted at the orchestrator level so its own
        # mandatory core (displacement + no-reclaim acceptance) is the sole gate, consistent
        # with the same double-gating lesson applied to donchian_trend_follow.
        "family": "session_liquidity_breakout", "default_activation": DISABLED,
        "timeframes": ("M15",), "regimes": (),
    },
    "fx_relative_momentum": {
        # 2026-08-24: new independent strategy family (cross-sectional currency momentum, see
        # backend/mt5_strategies/currency_strength.py's module docstring for the methodology
        # citation), same DISABLED-by-default, zero-live-footprint precedent. regimes empty --
        # the cross-sectional strength ranking is the strategy's own mandatory core; a separate
        # ctx.regime gate is not part of the design (explicit instruction: the ranking itself is
        # the core signal, not one more condition in an AND-chain).
        "family": "fx_relative_momentum", "default_activation": DISABLED,
        "timeframes": ("M15", "H1", "H4"), "regimes": (),
    },
    "donchian_imbalance": {
        # 2026-10-09: "combination B" (docs/donchian-imbalance-combo-check.md) -- Donchian breakout,
        # entered on the first touch of the imbalance zone the breakout left. DISABLED by default;
        # the live account runs it via MT5_STRATEGY_ACTIVATION_DONCHIAN_IMBALANCE=ACTIVE_MT5 as the
        # only active strategy. regimes empty -- its own volatility/HTF conditions are the gate.
        "family": "donchian_imbalance", "default_activation": DISABLED,
        "timeframes": ("M15", "H1"), "regimes": (),
    },
    "bsi": {
        # 2026-08-30: mentor-faithful SMC strategy family (Faiz course, 40-video transcript
        # extraction -- see backend/mt5_strategies/families/bsi.py's own module docstring
        # and the bsi_final_report.md this was built alongside). Same DISABLED-by-default,
        # zero-live-footprint precedent as wyckoff/donchian_trend_follow/session_liquidity_
        # breakout/fx_relative_momentum above -- the live DEMO backend's own evaluate_all() never
        # evaluates or shadow-tracks this until historical/OOS validation evidence is reported and
        # promotion is explicitly approved. Historical validation runs reach the real evaluator by
        # setting MT5_STRATEGY_ACTIVATION_BSI=SHADOW_MT5 as a process-local env var scoped
        # to the offline replay/backtest script's own process only -- never deployed to the live
        # container's .env/docker-compose. regimes deliberately empty -- the mentor's own
        # methodology gates on HTF bias/session timing/structure per-subtype internally, not a
        # separate ctx.regime axis (no mentor strategy across all 40 videos references an
        # ADX/volatility regime concept).
        "family": "bsi", "default_activation": DISABLED,
        "timeframes": ("M15", "H1", "H4"), "regimes": (),
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


# BSI Intelligence Migration -- per-subtype activation gating (2026-09-01). `STRATEGY_FAMILIES
# ["bsi"]` above is ONE switch for the whole 9-subtype family; the evidence gathered across this
# mission is explicitly uneven across subtypes (Under/Over is the strongest candidate researched
# so far; New York's own research found it not robust in the recent window; the near-zero
# subtypes are RARE-BUT-CORRECT but essentially untested at scale) -- promoting/demoting them as
# one unit would either hold back a subtype with real evidence behind it, or promote several with
# none. This is a SECOND, MORE GRANULAR gate NESTED UNDER the existing family-level one, not a
# replacement for it: `families/__init__.py::evaluate_all()` already skips calling `evaluate_bsi`
# ENTIRELY whenever `activation_status("bsi") == DISABLED` (the current, unchanged default) --
# so every function below is fully inert for live/DEMO evaluation until BOTH the family gate AND
# a specific subtype's own gate are separately, deliberately raised above DISABLED. Every subtype
# defaults to DISABLED here, matching wyckoff/donchian_trend_follow/bsi's own "new, unvalidated
# stays DISABLED until proven" precedent (default_activation=DISABLED above) -- reused, not
# invented. SCOPE NOTE, stated plainly: this gate governs CANDIDATE GENERATION inside
# evaluate_bsi()'s own subtype loop only (a DISABLED subtype's evaluator is never even called).
# It deliberately does NOT touch `evaluate_bsi_subtype()` (bsi_engine.py's direct, single-subtype
# entry point used by research/backtesting/replay) -- gating that would silently break every
# historical backfill and replay script this mission has built, which must remain able to
# evaluate any subtype regardless of its live activation state. It also does NOT itself enforce
# the ACTIVE_MT5-vs-SHADOW_MT5 distinction at the broker-order-placement layer -- every BSI
# signal's own `strategy_id` field is the single family id "bsi" (see bsi_engine.py's own
# `_STRATEGY_ID`), so the existing execution-layer code that already consults
# `activation_status("bsi")` before placing a real order has no way to see which SUBTYPE a given
# signal came from without a further, deliberately NOT-made-here change to that execution path.
# The subtype's own resolved status IS stamped onto every generated signal's evidence
# (`evidence["subtype_activation_status"]`) specifically so a future, explicit change to the
# execution layer can make that distinction correctly -- documented here as a known, intentional
# scope boundary, not silently glossed over.
_BSI_SUBTYPES = ("bsi_order_flow", "bsi_abc", "bsi_abcd", "bsi_asian", "bsi_new_york", "bsi_0930", "bsi_under_over", "bsi_reactionary", "bsi_ob_liquidity")
_BSI_SUBTYPE_DEFAULT_ACTIVATION: dict[str, str] = {subtype: DISABLED for subtype in _BSI_SUBTYPES}


def bsi_subtype_activation_status(subtype: str) -> str:
    """Per-subtype analog of activation_status() above -- identical precedence order (circuit
    breaker > env override > coded default), identical valid-value set, deliberately reusing the
    SAME circuit_breaker.is_tripped()/_VALID_OVERRIDES machinery rather than a parallel
    implementation. Env var: BSI_SUBTYPE_ACTIVATION_<SUBTYPE-WITHOUT-ITS-OWN-BSI_-PREFIX>, e.g.
    BSI_SUBTYPE_ACTIVATION_UNDER_OVER for subtype "bsi_under_over" (the "bsi_" prefix already
    implied by the BSI_SUBTYPE_ACTIVATION_ prefix itself, not repeated, matching the exact form
    requested). Unknown subtype strings resolve to DISABLED (fail closed, never fail open on a
    typo or a not-yet-registered subtype id)."""
    from backend.mt5_strategies import circuit_breaker

    if circuit_breaker.is_tripped(subtype):
        return DISABLED
    if subtype not in _BSI_SUBTYPES:
        return DISABLED
    suffix = subtype.upper().removeprefix("BSI_")
    override = os.getenv(f"BSI_SUBTYPE_ACTIVATION_{suffix}")
    if override in _VALID_OVERRIDES:
        return override
    return _BSI_SUBTYPE_DEFAULT_ACTIVATION.get(subtype, DISABLED)


# 2026-08-24: trend_pullback regime-gate widening, evidence-based (docs/mr-tp-evidence-
# validation-6month-report.md) -- a 6-month point-in-time-safe replay found `breakout` regime
# among trend_pullback's currently-blocked candidates outperforms even the strategy's own gated
# (trending_up/trending_down) population (+0.324R/PF 1.57, n=3485, vs +0.236R/PF 1.39, n=2575).
# Single window, no walk-forward split yet -- smallest possible activation: exactly ONE
# additional regime, reversible via MT5_TREND_PULLBACK_BREAKOUT_REGIME_ENABLED (default True;
# set False to instantly revert to the original trending_up/trending_down-only gate with no code
# change). Every other previously-blocked regime (ranging, reversal, low_volatility,
# high_volatility) remains blocked. Deliberately does NOT touch STRATEGY_FAMILIES["trend_
# pullback"]["regimes"] itself (documents the conservative baseline) -- this is a separate,
# explicit, evidence-gated addition on top, same pattern as _shared.py's _REGIME_DISABLED_
# STRATEGIES/_REGIME_BOOSTED_STRATEGIES tables (the separate, still-inert Phase-2 ADX axis,
# untouched by this change). Does not touch trend_pullback.py itself, mean_reversion, stop/
# target geometry, confidence, Historical Intelligence, Adaptive Manager, or risk controls.
_TREND_PULLBACK_EXTRA_REGIME_ENV = "MT5_TREND_PULLBACK_BREAKOUT_REGIME_ENABLED"


def regime_compatible(strategy_id: str, regime: str) -> bool:
    """Part 5: do not make every strategy trade every regime. An empty `regimes` tuple (only
    mtfai1) means unrestricted, preserving its current, unchanged production behavior."""
    meta = STRATEGY_FAMILIES.get(strategy_id)
    if not meta or not meta.get("regimes"):
        return True
    if strategy_id == "trend_pullback" and regime == "breakout":
        raw = os.getenv(_TREND_PULLBACK_EXTRA_REGIME_ENV)
        return True if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}
    return regime in meta["regimes"]


def all_strategy_ids(*, exclude: tuple[str, ...] = ("mtfai1",)) -> list[str]:
    return [sid for sid in STRATEGY_FAMILIES if sid not in exclude]

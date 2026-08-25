"""Bensim -- Adaptive Manager V3, Parts 4-7: strategy-specific management policy profiles.

ONE Adaptive Manager, not separate managers per strategy (explicit instruction) -- this module
only supplies PARAMETERS that the existing, unmodified decision logic in service.py already
reads via _env_float/_env_int calls (ADAPTIVE_BREAKEVEN_R, ADAPTIVE_PARTIAL_PROFIT_R,
ADAPTIVE_TRAIL_R, the MFE-protection partial-close fraction). Every threshold this module can
override already exists as a global env default; a strategy with no profile entry (or a
profile entry missing a specific key) falls through to that exact same global default, so
activating this module changes NOTHING for a strategy this table doesn't mention.

Provenance for the profiles below -- deliberately NOT invented from scratch for every strategy:
  - mtfai1: PRESERVES the existing, already-proven MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED/_FRACTION
    override verbatim (50/50 split; direct counterfactual replay of 34 real fires found this
    beats both full-close (+0.36R actual vs +0.76R had-it-stayed-open) and untouched-hold on
    worst-case downside -- see autonomous.py's own MTFAI1 V2 forensic-audit comments). This
    module generalizes the MECHANISM (a strategy_id-keyed lookup) without changing mtfai1's own
    number.
  - trend_pullback: "tolerate healthy retracements, allow a runner while trend structure holds"
    (Part 7's own written brief) -- a trend-following strategy's whole thesis is that pullbacks
    are noise INSIDE a real move, so its breakeven/trail thresholds are set LATER (higher R) than
    the global default, giving genuine trend continuation more room before the manager locks in
    or trails tightly. No forward MFE-partial evidence exists yet for this strategy specifically
    -- mfe_partial defaults to the existing global (full-close) behavior rather than guessing a
    split with no evidence behind it.
  - mean_reversion: "monetize the move back to equilibrium quickly, don't hold for a giant
    runner" (Part 7's own written brief) -- a mean-reversion trade's edge is realized as price
    approaches the mean/equilibrium, not by trending further, so breakeven/partial-profit
    thresholds are set EARLIER (lower R) than the global default -- consistent with this
    strategy's own already-graduated, RSI-extremity-based entry design (unlike trend_pullback's
    flat-strength signal) rewarding a fast, decisive exploitation of a real but typically
    shorter-lived edge.
  - vwap_reversion: same "capture near equilibrium, don't hold like a trend trade" logic as
    mean_reversion (both are reversion-to-a-reference-level strategies), reused rather than
    reinvented -- and this is the strategy this session's own diagnostic work found has a REAL
    entry edge but historically gives most of it back to weak management (best-in-class MFE of
    all 12 strategies, but -0.06R actual live vs +0.13-0.15R simple fixed-R alternatives) -- the
    single strongest evidence-backed case in the whole registry for why management, not entry,
    needs to change here.
  - Every other strategy (smc_continuation, support_resistance_bounce, session_breakout,
    breakout, ema_trend, liquidity_sweep_reversal, momentum, and any future addition): NO entry
    in this table -- falls through to today's exact global-default behavior, unchanged. These
    strategies were only just activated for DEMO (see the same-session risk-tier work) with no
    real forward management evidence yet; inventing a personality for them now would be
    unvalidated guessing, not the evidence-based design this directive asks for. Extend this
    table once real forward evidence (Part 11/12's per-strategy STATIC vs MANAGED attribution)
    justifies a specific profile for one of them.

Every number here is reversible via the SAME env-var names service.py already reads
(MT5_STRATEGY_PROFILE_<ID>_<PARAM>), never a new parallel config system.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "off", "no"}


@dataclass(frozen=True)
class StrategyManagementProfile:
    """Every field is Optional-by-omission: a None/absent value means "use service.py's own
    existing global env default", never a fabricated number. mfe_partial_fraction/enabled
    generalizes the mechanism MTFAI1 V2 already proved; the R-thresholds parametrize the
    breakeven/partial-profit/trail gates that already exist in _evaluate_position, unchanged."""
    breakeven_r: float | None = None
    partial_profit_r: float | None = None
    partial_profit_fraction: float | None = None
    trail_r: float | None = None
    mfe_partial_enabled: bool = False
    mfe_partial_fraction: float = 0.5


# Base (evidence-driven) profiles, before env overrides are layered on top. mtfai1's
# mfe_partial fields are intentionally NOT hardcoded here -- get_profile() reads them fresh from
# the ORIGINAL MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED/_FRACTION env vars every call (the exact same
# ones autonomous.py/service.py already used pre-generalization), so mtfai1's real, live,
# already-proven behavior (including "off unless explicitly enabled") is preserved byte-for-byte
# -- a static True/0.5 here would have silently ignored an operator turning that flag off.
_BASE_PROFILES: dict[str, StrategyManagementProfile] = {
    "trend_pullback": StrategyManagementProfile(
        breakeven_r=1.5, trail_r=2.0,  # later than the 1.0/1.5 global default -- give a real trend more room before locking in
    ),
    "mean_reversion": StrategyManagementProfile(
        breakeven_r=0.6, partial_profit_r=0.35, partial_profit_fraction=0.4,  # earlier than 1.0/0.5 global -- capture the reversion move while it's there
        mfe_partial_enabled=True, mfe_partial_fraction=0.6,  # weight toward locking in more, less runner -- this is not a trend strategy
    ),
    "vwap_reversion": StrategyManagementProfile(
        breakeven_r=0.6, partial_profit_r=0.35, partial_profit_fraction=0.4,
        mfe_partial_enabled=True, mfe_partial_fraction=0.6,
    ),
}


def _resolve_field(strategy_id: str, field_name: str, base_value: float | bool | None, env_suffix: str, *, is_flag: bool = False):
    env_name = f"MT5_STRATEGY_PROFILE_{strategy_id.upper()}_{env_suffix}"
    raw = os.getenv(env_name)
    if raw is None:
        return base_value
    if is_flag:
        return raw.strip().lower() not in {"false", "0", "off", "no"}
    try:
        return float(raw)
    except (TypeError, ValueError):
        return base_value


def get_profile(strategy_id: str | None) -> StrategyManagementProfile:
    """Returns the effective profile for `strategy_id`: base evidence-driven values from
    _BASE_PROFILES (empty profile -- i.e. every field None/default-off -- for any strategy not
    listed there), with any MT5_STRATEGY_PROFILE_<ID>_<PARAM> env override layered on top,
    per-field. Never raises; an unrecognized/missing strategy_id returns the all-None default
    profile, which _evaluate_position's own resolver treats as "use the existing global
    default" for every threshold."""
    sid = (strategy_id or "").lower()
    base = _BASE_PROFILES.get(sid, StrategyManagementProfile())
    if sid == "mtfai1":
        # Preserves the original, already-proven env vars verbatim -- see the module docstring.
        mfe_partial_enabled = _env_flag("MT5_MTFAI1_V2_MFE_PARTIAL_ENABLED", False)
        mfe_partial_fraction = _env_float("MT5_MTFAI1_V2_MFE_PARTIAL_FRACTION", 0.5)
    else:
        mfe_partial_enabled = bool(_resolve_field(sid, "mfe_partial_enabled", base.mfe_partial_enabled, "MFE_PARTIAL_ENABLED", is_flag=True))
        mfe_partial_fraction = float(_resolve_field(sid, "mfe_partial_fraction", base.mfe_partial_fraction, "MFE_PARTIAL_FRACTION"))
    return StrategyManagementProfile(
        breakeven_r=_resolve_field(sid, "breakeven_r", base.breakeven_r, "BREAKEVEN_R"),
        partial_profit_r=_resolve_field(sid, "partial_profit_r", base.partial_profit_r, "PARTIAL_PROFIT_R"),
        partial_profit_fraction=_resolve_field(sid, "partial_profit_fraction", base.partial_profit_fraction, "PARTIAL_PROFIT_FRACTION"),
        trail_r=_resolve_field(sid, "trail_r", base.trail_r, "TRAIL_R"),
        mfe_partial_enabled=mfe_partial_enabled,
        mfe_partial_fraction=mfe_partial_fraction,
    )


def strategy_param(strategy_id: str | None, profile_field: str, global_default: float) -> float:
    """Convenience resolver for service.py's threshold read-sites: returns the strategy's
    profile value for `profile_field` if the profile sets one, otherwise `global_default`
    (whatever service.py's own _env_float(...) call would have returned) -- so a strategy with
    no profile, or a profile that leaves this one field unset, is byte-identical to today."""
    profile = get_profile(strategy_id)
    value = getattr(profile, profile_field, None)
    return global_default if value is None else float(value)

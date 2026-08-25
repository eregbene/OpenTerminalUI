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
  - smc_continuation: real entry code read directly (evaluate_smc_continuation) -- H4 trend ->
    with-trend M15 BOS -> displacement confirmation -> optional FVG/OB retracement zone, fixed
    3.0xATR target. A genuine continuation thesis, same family as trend_pullback, but with a
    weaker/more-recent-only edge (+0.036R pooled 8yr, accelerating to +0.474R in 2026) -- give
    real but slightly less room than trend_pullback's own (proven, longer-running) edge earns.
  - liquidity_sweep_reversal: real entry code read directly (evaluate_liquidity_sweep_reversal)
    -- the ENTRY ITSELF already requires the full sweep -> displacement -> CHoCH/MSS sequence
    (never just a naive reversal candle), so a valid position already carries strong, pre-
    confirmed structural evidence. Real record is encouraging (+0.259R, PF2.52) but n is small
    -- per the explicit instruction not to over-manage a temporary retrace, thresholds stay at
    the standard global default (no earlier/later override) rather than guessing a number this
    strategy hasn't earned or lost evidence for either way.
  - support_resistance_bounce: real entry code read directly -- price within ATR-scaled
    tolerance of a swing-derived LiquidityLevel with a rejecting candle, fixed 2.2xATR target
    (already the most modest target of any strategy in the registry). Tier C, pooled record
    -0.122R. Per the explicit instruction ("earlier partial profit... not a large runner"),
    reuses mean_reversion's earlier-capture posture -- a bounce's whole thesis resolves quickly
    or not at all, same as a reversion trade.
  - session_breakout: real entry code read directly (evaluate_session_breakout) -- breaks a
    session/prior-day high or low, fixed 2.5xATR target, no acceptance/retest confirmation
    logic in the entry at all (fires the instant price crosses the level, full stop). Tier C,
    pooled record is the worst in the registry (-0.296R, PF0.44) -- tighter-than-default
    breakeven/trail (lock in sooner) AND the new structural-reacceptance check below (a genuine
    close back inside the broken range, exactly the "close/reacceptance" failure mode named in
    the brief) enabled, since the entry has no failure-detection of its own to lean on.
  - breakout: real entry code read directly (evaluate_breakout) -- requires a real M15 BOS, two
    distinct entry modes (break-and-go / retest-and-hold), and ALREADY has a structural-
    take-profit lever (_structural_take_profit) available (flagged, off by default). Pooled
    record is negative (-0.183R) but the REAL manager-delta measured this session was positive
    (+0.380R) -- i.e. today's GENERIC management is already helping this strategy. Deliberately
    left with NO breakeven/trail override (global default, unchanged) so as not to disturb
    whatever the generic logic is already doing right; only the new structural-reacceptance
    check is added (same real failure mode as session_breakout -- a break that returns inside
    its own range -- but this strategy's own entry logic is meaningfully different, so its
    behavior is measured and reported separately, never assumed identical to session_breakout's).
  - ema_trend: real entry code read directly -- EMA20/50/100 stack alignment (a trend-bias
    strategy, structurally closest to trend_pullback/smc_continuation despite its own catastrophic
    2025-2026 collapse, -0.130R pooled, ROOT-CAUSED in-code as "zero market-structure involvement
    in its trigger" per the strategy's own module docstring). Per the explicit instruction ("more
    breathing room than reversion trades... do not use opposing candles as invalidation"): same
    trend-family posture as trend_pullback, not tightened despite the weak record, since the
    record's own diagnosis is entry-signal quality, not management being too loose. The naive
    "opposing candles" thesis-invalidation rule is confirmed suppressed system-wide already
    (ADAPTIVE_THESIS_INVALIDATION_ENABLED=false, verified against the live container) -- nothing
    additional needed to satisfy that instruction.
  - momentum: excluded from this table entirely -- not DEMO-activated (see the same-session
    risk-tier work), so no management profile is relevant.

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
    # Part 5/6 (session_breakout/breakout): a genuine close back inside the ORIGINAL broken
    # range is stronger invalidation evidence than a generic candle-count rule -- see
    # get_structural_reacceptance_price/service.py's own new candidate-generation branch. Off
    # by default (every strategy this table doesn't set it for is unaffected); the reference
    # price itself comes from AdaptivePositionStateORM.original_structural_reference (captured
    # once at entry from the real originating candidate's own geometry, never re-detected).
    structural_reacceptance_enabled: bool = False


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
    "smc_continuation": StrategyManagementProfile(
        breakeven_r=1.3, trail_r=1.8,  # continuation family, real but weaker/newer edge than trend_pullback -- real room, not as much
    ),
    "support_resistance_bounce": StrategyManagementProfile(
        breakeven_r=0.6, partial_profit_r=0.35, partial_profit_fraction=0.4,  # same earlier-capture posture as mean_reversion -- a bounce resolves quickly or not at all
        mfe_partial_enabled=True, mfe_partial_fraction=0.6,
    ),
    "session_breakout": StrategyManagementProfile(
        breakeven_r=0.8, trail_r=1.3,  # tighter than global default -- worst pooled record in the registry, entry has no failure-detection of its own
        structural_reacceptance_enabled=True,
    ),
    "breakout": StrategyManagementProfile(
        structural_reacceptance_enabled=True,  # no breakeven/trail override -- generic management already measured +0.380R delta here, don't disturb it
    ),
    "ema_trend": StrategyManagementProfile(
        breakeven_r=1.5, trail_r=2.0,  # trend family, same posture as trend_pullback -- weak record is an entry-signal-quality problem, not a management-too-loose one
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
        structural_reacceptance_enabled=bool(_resolve_field(sid, "structural_reacceptance_enabled", base.structural_reacceptance_enabled, "STRUCTURAL_REACCEPTANCE_ENABLED", is_flag=True)),
    )


def strategy_param(strategy_id: str | None, profile_field: str, global_default: float) -> float:
    """Convenience resolver for service.py's threshold read-sites: returns the strategy's
    profile value for `profile_field` if the profile sets one, otherwise `global_default`
    (whatever service.py's own _env_float(...) call would have returned) -- so a strategy with
    no profile, or a profile that leaves this one field unset, is byte-identical to today."""
    profile = get_profile(strategy_id)
    value = getattr(profile, profile_field, None)
    return global_default if value is None else float(value)

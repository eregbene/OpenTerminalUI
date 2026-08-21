"""One-time seed: initialize strategy_lifecycle_states for all 12 strategies using the evidence
already gathered across Priorities 1-5.5 this session, per the user's own explicit Priority 6
posture. Idempotent (initialize_state never overwrites an existing row)."""
from __future__ import annotations

from backend.mt5_strategies import lifecycle as lc

SEED = [
    ("mean_reversion", lc.ACTIVE, "Tier A protected (Priority 4): 8-year audit ROBUST_CORE_EDGE, edge_stability=STRONG, walk-forward passes in every window re-run. Currently ACTIVE_MT5, no evidence contradicts it."),
    ("trend_pullback", lc.ACTIVE, "Tier A protected (Priority 4): 8-year audit ROBUST_CORE_EDGE, edge_stability=ACCEPTABLE, walk-forward passes. Currently ACTIVE_MT5, no evidence contradicts it."),
    ("vwap_reversion", lc.SHADOW, "Priority 2: entry edge confirmed (best-in-class MFE profile of all 12 strategies), but current management gives most of it back. Simpler candidate policies (breakeven_at_0_75r_v1, mfe_retrace_50_after_0_75r_v1) already accumulating forward shadow evidence. Not promotable until that evidence is sufficient."),
    ("smc_continuation", lc.SHADOW, "Priority 4: whole-strategy walk-forward failed, but TRENDING-only restriction validated robust (walk-forward train +0.126R -> OOS +0.336R, sign agree). Filter not yet implemented in code -- SHADOW pending that implementation and its own forward validation."),
    ("liquidity_sweep_reversal", lc.SHADOW, "Priority 4: whole-strategy walk-forward failed, but 4-symbol restriction (NZDUSD/EURJPY/USDCAD/GBPJPY) validated robust (walk-forward train +0.413R -> OOS +0.315R, sign agree). Filter not yet implemented in code -- SHADOW pending that implementation."),
    ("mtfai1", lc.SHADOW, "Priority 3: no broad live edge (full-corpus walk-forward FAILED_OOS, never solidly positive in 8 years). No simple regime/direction filter survived proper multi-window + walk-forward validation. SHADOW_ONLY per the audit and Priority 3's own conclusion -- no further rescue attempts."),
    ("breakout", lc.RETIRED, "Priority 4: audit classification NO_CREDIBLE_EDGE/DECAYED_EDGE, severe 2024-26 collapse (PF gross 0.19), reproducible negative walk-forward. No implementation bug found. Kept SHADOW-tracked (not DISABLED) for ongoing research/regime-study data, matching Priority 4's stated intent."),
    ("ema_trend", lc.RETIRED, "Priority 4: audit classification DECAYED_EDGE/NO_CREDIBLE_EDGE, 2026 win rate 1.3% (n=1,883). No implementation bug found. Kept SHADOW-tracked for research."),
    ("momentum", lc.RETIRED, "Priority 4: audit classification NEGATIVE_EDGE (reproducible walk-forward failure). Already fully non-executing via its own MT5_MOMENTUM_STRATEGY_ENABLED circuit breaker, independent of this lifecycle state."),
    ("session_breakout", lc.SHADOW, "Demoted from ACTIVE_MT5 in an earlier pass (before this session's Priority-numbered work); status not re-litigated this session per the user's 'do not repeat broad audits' instruction -- carried forward as SHADOW."),
    ("support_resistance_bounce", lc.SHADOW, "Audit classification DECAYED_EDGE: 6 years genuinely robust (2018-2024, walk-forward STRONG), sharp version-independent breakdown since 2025. Already demoted to SHADOW_MT5 in an earlier pass; carried forward as SHADOW pending evidence the 2025-26 regime shift has reversed."),
    ("wyckoff", lc.RESEARCH, "Newest strategy family (registered 2026-08-17), deliberately zero live footprint by design pending explicit historical/OOS validation. Sample breadth/history insufficient for promotion. Matches its own registration commit's DISABLED default exactly."),
]

for strategy_id, state, reason in SEED:
    result = lc.initialize_state(strategy_id, state, reason=reason)
    print(f"{strategy_id}: {result['lifecycle_state']} (mapped_activation={result['mapped_activation']})")

"""Mechanical, byte-identical extraction of the strategy families not otherwise given their own
module (`liquidity_sweep_reversal`, `session_breakout`, `wyckoff`) out of the pre-restructure
`backend/mt5_strategies/families.py` monolith (see that file's git history, last version before
the 2026-08-20 split: commit 387e4ed).

Every function body below is copied verbatim -- same logic, same reasons, same evidence keys,
same stop/target math. The ONLY changes from the original module are import-path updates: the
shared helpers (`_closes`, `_dynamic_stop`, `_signal`, `_no_signal`, `_eqh_eql_touch_count`,
`_squeeze_evidence`, `_geometry_metadata`) now come from `families/_shared.py` instead of being
defined in this same file.

Extraction history: `trend_pullback`, `breakout`, and `smc_continuation` were rewritten in the
2026-08-20 (Phase 1) restructure; `ema_trend` and `mean_reversion` were extracted into their own
modules in the 2026-08-21 (Phase 2) restructure for their new ADX regime gates; `vwap_reversion`,
`support_resistance_bounce`, and `momentum` were extracted into their own modules in the
2026-08-21 (Phase 3) restructure (session-anchor bug fix, HTF gate, deprecation circuit breaker
respectively) -- none of those eight are here.

`session_breakout` is still here unmodified even though the Phase 2 spec's session-timing table
names it, and the Phase 3 spec's IDM precondition (Section 3) was scoped to `breakout.py` and
`session_breakout.py` together -- wiring either gate into this specific evaluator was out of scope
for the Phase 2 and Phase 3 deliverables' requested output-file lists; `_shared.py`'s gate
functions (`_session_timing_permits`, `_liquidity_sweep_precedes`) already key correctly on
strategy_id / accept an explicit trend_direction, so wiring them in later is additive, not a
redesign. `liquidity_sweep_reversal` carries a standing do-not-touch directive from the original
8-year audit. `wyckoff` is confirmed isolated (default DISABLED activation) and was never in scope
for any phase's structural-TP propagation list.
"""
from __future__ import annotations

from decimal import Decimal

from backend.market_structure.models import StructureBreakKind
from backend.market_structure.wyckoff import analyze_wyckoff
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _signal,
    _squeeze_evidence,
)
from backend.mt5_strategies.models import StrategySignal

__all__ = [
    "evaluate_liquidity_sweep_reversal",
    "evaluate_session_breakout",
    "evaluate_wyckoff",
]


# ----------------------------------------------------------------- liquidity_sweep_reversal ---
def evaluate_liquidity_sweep_reversal(ctx: StrategyContext) -> StrategySignal:
    """Genuine sweep -> displacement -> CHoCH/MSS sequencing (Part 7's worked example),
    composed entirely from already-detected backend.market_structure objects -- the sequencing
    itself, checked here, is the one piece the audit found nowhere in the engine."""
    sweeps = ctx.m15_snapshot.liquidity_sweeps
    if not sweeps:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_liquidity_sweep")
    latest_sweep = max(sweeps, key=lambda s: s.bar_index)
    reversal_direction = latest_sweep.direction  # "bullish" (swept sell-side) or "bearish" (swept buy-side)
    subsequent_displacement = [d for d in ctx.m15_snapshot.displacements if d.bar_index >= latest_sweep.bar_index and d.direction == reversal_direction]
    if not subsequent_displacement:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_displacement_after_sweep")
    displacement = min(subsequent_displacement, key=lambda d: d.bar_index)
    structure_shift = [
        b for b in ctx.m15_snapshot.breaks
        if b.bar_index >= displacement.bar_index and b.direction == reversal_direction and b.break_kind in {StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value}
    ]
    if not structure_shift:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_structure_shift_after_displacement")
    shift = min(structure_shift, key=lambda b: b.bar_index)
    bars_since_shift = (len(ctx.m15_rows) - 1) - shift.bar_index
    if bars_since_shift > 5:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="structure_shift_too_old")
    direction = "LONG" if reversal_direction == "bullish" else "SHORT"
    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, latest_sweep.swept_price, atr)
    if stop is None:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    is_mss = shift.break_kind == StructureBreakKind.MSS.value
    strength = 68.0 + (17.0 if is_mss else 5.0)
    # EQH/EQL evidence-only (n=15 on the real EURUSD OOS validation -- far too small to trust a
    # strength adjustment): was the level this strategy's own sweep touched ALSO a repeated-touch
    # EQH/EQL pool (cross-referencing the separate equal_level_sweeps list by bar/side), not just
    # a single-touch swing.
    swept_level_is_eqh_eql = any(s.bar_index == latest_sweep.bar_index and s.side == latest_sweep.side for s in ctx.m15_snapshot.equal_level_sweeps)
    evidence = {
        "sweep_id": latest_sweep.id, "displacement_id": displacement.id, "structure_shift_id": shift.id,
        "structure_shift_kind": shift.break_kind, "bars_since_shift": bars_since_shift,
        "swept_level_is_eqh_eql": swept_level_is_eqh_eql,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, latest_sweep.swept_price, atr, 1.0, 3.0))


# ------------------------------------------------------------------------ session_breakout ---
def evaluate_session_breakout(ctx: StrategyContext) -> StrategySignal:
    """Breakout beyond the most recently completed session's high/low (Asian/London/NY,
    whichever the market_structure engine's configured session windows produced), or the
    previous day's high/low -- levels already computed by backend.market_structure.sessions,
    never re-derived here."""
    levels = ctx.m15_snapshot.session_levels
    if not levels:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason="no_session_levels")
    price = float(_closes(ctx.m15_rows).iloc[-1])
    highs = [lv for lv in levels if lv.level_name in {"high", "previous_day_high"}]
    lows = [lv for lv in levels if lv.level_name in {"low", "previous_day_low"}]
    broken_high = max((lv for lv in highs if price > float(lv.level)), key=lambda lv: lv.level, default=None)
    broken_low = min((lv for lv in lows if price < float(lv.level)), key=lambda lv: lv.level, default=None)
    if broken_high is None and broken_low is None:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason="no_session_level_broken")
    direction = "LONG" if broken_high is not None else "SHORT"
    reference = broken_high if broken_high is not None else broken_low
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, reference.level, atr)
    if stop is None:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "session": reference.session_name, "level_name": reference.level_name, "level": float(reference.level),
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))  # observability only -- see _squeeze_evidence's docstring
    return _signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", direction=direction, strength=64.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, reference.level, atr, 1.0, 3.0))


# --------------------------------------------------------------------------------- wyckoff ---
def evaluate_wyckoff(ctx: StrategyContext) -> StrategySignal:
    """Wyckoff accumulation/distribution schematic strategy -- an INDEPENDENT strategy family
    (not a label added to an existing one). All schematic/phase/event detection lives in
    backend.market_structure.wyckoff::analyze_wyckoff (point-in-time-safe, itself built only on
    the same swings/structure-breaks/liquidity-sweeps every other family here already reuses --
    see that module's docstring for the volume-representation and ~100-bar-window caveats). This
    function only decides, given that analysis, whether a TRADEABLE setup exists right now.

    Two explicit, separately-tagged setups (evidence["setup"]):
      spring_sos_lps       -- Spring (accumulation) or Upthrust/UTAD (distribution) has occurred
                               AND been successfully tested (phase reached C or later -- a failed
                               test means analysis.invalidated and this function already returned
                               NO_TRADE above), AND Sign-of-Strength/Weakness has confirmed (phase
                               D/E). Entered at the LPS/LPSY pullback price when that swing has
                               already confirmed, or, if it hasn't confirmed yet, at a live
                               pullback currently holding the just-broken SOS/SOW level within one
                               ATR -- avoids waiting for a swing's own right-bar confirmation delay
                               to chase an entry that has already run away.
      phase_d_continuation -- SOS/SOW has confirmed (phase D or E) but price is NOT at a specific
                               pullback right now -- a plain continuation entry. Deliberately
                               tagged separately from spring_sos_lps so historical validation can
                               tell whether this weaker-evidence entry independently holds up
                               rather than assuming it shares the other setup's edge.

    A schematic with no Automatic Rally yet, no climax at all, an invalidated Spring/UTAD (broke
    past the tested extreme), or no SOS/SOW confirmation yet (phase A/B/C_untested/C) produces
    NO_TRADE -- this strategy only trades a schematic that has already earned BOTH a successful
    extreme test AND a strength/weakness confirmation, never an in-progress range on spec."""
    analysis = analyze_wyckoff(ctx.m15_rows, ctx.m15_snapshot, symbol=ctx.broker_symbol, timeframe="M15")
    if analysis.schematic == "none":
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason="no_schematic_detected")
    if analysis.invalidated:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=analysis.invalidation_reason or "spring_or_utad_invalidated")
    if analysis.phase not in {"D", "E"}:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=f"phase_{analysis.phase}_not_yet_tradeable")

    direction = "LONG" if analysis.schematic == "accumulation" else "SHORT"
    sos_event = analysis.event("SOS") if direction == "LONG" else analysis.event("SOW")
    if sos_event is None:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason="no_sos_sow_confirmation")

    price = float(_closes(ctx.m15_rows).iloc[-1])
    atr = ctx.atr_m15 or Decimal("0.0001")
    atr_f = float(atr)
    tolerance = atr_f * 1.0

    lps_event = analysis.event("LPS") if direction == "LONG" else analysis.event("LPSY")
    spring_event = analysis.event("SPRING") if direction == "LONG" else analysis.event("UTAD")
    broken_level = sos_event.evidence.get("broken_level")

    at_lps = lps_event is not None and abs(price - lps_event.price) <= tolerance
    holding_broken_level = broken_level is not None and (
        (direction == "LONG" and price >= broken_level and (price - broken_level) <= tolerance)
        or (direction == "SHORT" and price <= broken_level and (broken_level - price) <= tolerance)
    )

    if at_lps or holding_broken_level:
        setup = "spring_sos_lps"
        structure_ref = lps_event.price if lps_event is not None else (broken_level if broken_level is not None else (spring_event.price if spring_event is not None else None))
        min_mult, max_mult = 1.0, 3.0
    else:
        setup = "phase_d_continuation"
        structure_ref = broken_level if broken_level is not None else (spring_event.price if spring_event is not None else None)
        min_mult, max_mult = 1.5, 3.0

    entry = Decimal(str(price))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, structure_ref, atr, min_atr_mult=min_mult, max_atr_mult=max_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=stop_reason)

    # Wyckoff-native target: project the trading range's own width ("cause") from the breakout as
    # the expected move ("effect") -- a simplified point-and-figure-style count using data this
    # module already has, rather than a bare ATR multiple. Floored at 2x ATR so a narrow range
    # never produces a target inside ordinary noise.
    range_span = (analysis.range_high - analysis.range_low) if (analysis.range_high is not None and analysis.range_low is not None) else 0.0
    projected_move = Decimal(str(max(range_span, atr_f * 2.0)))
    target = entry + projected_move if direction == "LONG" else entry - projected_move

    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    strength = 62.0 + (15.0 if setup == "spring_sos_lps" else 0.0) + (10.0 if lps_event is not None or analysis.phase == "D" else 0.0)
    evidence = {
        "setup": setup, "schematic": analysis.schematic, "phase": analysis.phase,
        "range_low": analysis.range_low, "range_high": analysis.range_high, "range_position": analysis.range_position,
        "events": [{"type": e.event_type, "bar_index": e.bar_index, "price": e.price, "evidence": e.evidence} for e in analysis.events],
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr_f),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, structure_ref, atr, min_mult, max_mult))

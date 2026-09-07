"""bsi -- Faiz SMC course, mentor-faithful implementation, SHADOW/DISABLED-only.

Source: 40 course-video transcripts, extracted rule-by-rule into
D:\\ClaudeTemp\\claude\\d--Devops-trading-OpenTerminalUI\\b1fff0a5-89fe-4da5-950b-2fa3629b1c4c\\
scratchpad\\mentor_notes_*.md and cross-mapped against Bensim's existing market_structure engine
in part7_8_9_synthesis.md (24-row MENTOR RULE | BENSIM IMPLEMENTATION | MATCH QUALITY | ACTION
table). This module is the direct implementation of that table's "Action" column -- every
non-trivial choice below cites the row it comes from. Do not re-derive the mentor's rules from
this file's comments alone; the *_notes_*.md files carry the exact quotes and reasoning.

ARCHITECTURE (Part 8 of the synthesis doc, confirmed by direct mentor evidence, not assumed): ONE
strategy family with setup subtypes, not 9 independent strategies -- the mentor himself describes
Order Flow as "everything we've learned so far... used together" (the generic base-framework
application), ABCD as literally "a New York session trading strategy" composed onto a completed
ABC, and both Reactionary Block and Order Block Liquidity as Order Flow "with more
confirmation"/"a little twist". Every subtype below reuses the SAME shared toolkit (structure
break objects, FVG objects, liquidity objects, _dynamic_stop, _opposing_structural_level) and
differs only in: which swing/leg geometry identifies the setup, which timing/session gate
applies, whether HTF bias is required, and which target/RR discipline is used.

Registered subtypes (module-level evaluators, each independently callable for Part 11's
component-attribution backtesting): bsi_order_flow (base), bsi_abc, bsi_asian, bsi_new_york,
bsi_0930, bsi_under_over, bsi_abcd (= abc completion + a bsi_new_york-shaped D-leg, per the
mentor's own explicit "this IS a New York session trading strategy" statement). bsi_reactionary
and bsi_ob_liquidity are implemented as an `entry_confirmation_mode` flag ("reactionary" /
"ob_liquidity") layered on top of bsi_order_flow's own entry-array selection, per the synthesis
doc's own resolution of that open question (both are self-described by the mentor as "Order Flow
+ an extra confirmation layer", not independent setup shapes).

The SINGLE registered strategy_id/EVALUATORS entry is `evaluate_bsi` -- it tries subtypes
in BSI_SUBTYPE_ORDER (env, comma list, default "bsi_order_flow,bsi_abc,bsi_asian,
bsi_new_york,bsi_under_over,bsi_0930,bsi_abcd") and returns the first one that produces a valid
signal, tagging evidence["setup_subtype"]. Each subtype's own evaluator function is also
individually importable (`evaluate_bsi_order_flow`, etc.) for backtesting/attribution runs
that need to test ONE subtype in isolation without the others contending for the same cycle.

DELIBERATE, EXPLICIT DESIGN CHOICES documented once here rather than repeated at every call site:

1. Bensim's CHoCH-vs-MSS split (displacement-gated) does NOT exist in the mentor's own
   vocabulary -- he explicitly, verbally equates them ("market structure shift, OR change of
   character, whatever you want to call it", mentor_notes_asian_session.md). Every subtype's
   base MSS check therefore accepts Bensim's CHOCH *or* MSS classification, EXCEPT ny_0930,
   which the transcripts show him adding a displacement/momentum quality filter to specifically
   ("when market structure shift happens, it should happen WITH DISPLACEMENT" -- row 2's own
   stated exception) -- ny_0930 requires Bensim's stricter, already displacement-gated `MSS`
   value alone.
2. Fibonacci/premium-discount anchor = the SPECIFIC impulsive/structure-breaking leg, start to
   end (row 4) -- NOT `dealing_range.py::build_dealing_ranges()`'s "freshest swing high + freshest
   swing low, regardless of chronological adjacency" anchor, which the synthesis doc flags as a
   real mismatch. `_leg_bounds()` below is new, mentor-faithful logic built specifically for this
   module; `dealing_range.py` itself is untouched.
3. Order block = the FIRST candle of the SPECIFIC 3-candle sequence forming a given FVG (row 9) --
   the mentor explicitly and repeatedly rejects the generic "last opposite candle before the
   break" rule Bensim's own `zones.py::detect_order_blocks()` implements ("that is ABSOLUTELY
   WRONG"). `_mentor_order_block_for_fvg()` below derives the OB directly from an
   already-detected FVG's own `supporting_bar_indexes[0]` -- no new detector, `zones.py` is
   untouched, and this module never calls `detect_order_blocks()`.
4. Premium/discount = exactly the 0.5 midpoint of the leg from (2) above, nothing finer (row 5) --
   this module does NOT reuse `PremiumDiscountZone`/`build_dealing_ranges()`'s OTE zone (two
   internally-inconsistent OTE definitions already exist elsewhere in the engine; no mentor
   strategy across all 40 videos was found using OTE).
5. Every numeric threshold with no mentor-stated number (FVG "small vs big" size cutoff, fakeout
   "small vs big" size cutoff, SL ATR bounds, retest tolerance) is Bensim's OWN, explicitly
   labeled operationalization of a qualitative mentor rule -- each is env-tunable
   (MT5_BSI_*) without a code change, the same pattern `minimum_break_atr`/
   `minimum_size_atr` already establish elsewhere in this engine. These are never presented as
   mentor-sourced numbers.
6. Bensim's execution model here is signal-then-market-order (every existing family enters at the
   current M15 close, never a resting limit order at a theoretical zone edge) -- this module
   follows that same convention rather than inventing a new order type. The mentor's own
   "always enter a little shy of the exact level to deal with spreads" rule is therefore captured
   as (a) `_spread_within_safety_buffer` gating out abnormal-spread cycles entirely and (b)
   `_dynamic_stop`'s own existing spread-aware buffer on the STOP side, rather than as a
   synthetic limit-entry price -- documented here explicitly rather than silently dropped.
7. Take-profit discipline is genuinely per-subtype, not one shared rule (row 14, repeatedly
   confirmed): order_flow/asian_session/under_over = natural next opposing liquidity pool
   (`_opposing_structural_level`, unbounded); new_york_session/abcd = fixed 1:2 RR (his own
   explicit, repeated "always always always 1:2" rule); abc = the B-leg's own extreme price
   (a literal, well-evidenced rule -- see `_evaluate_abc`'s own docstring for the geometric
   reasoning); ny_0930 = bounded [3R, 5R] band.
8. SHADOW-only, never wired to live activation: registered in STRATEGY_FAMILIES with
   `default_activation=DISABLED` (Part 16's own zero-live-footprint precedent for every other
   recently-added, not-yet-validated family -- wyckoff/donchian_trend_follow/
   session_liquidity_breakout/fx_relative_momentum all use this same pattern; SHADOW_MT5 is only
   ever set as a process-local env var scoped to an offline validation script's own process, per
   those same modules' precedent). This is at least as conservative as, and matches the CURRENT
   codebase convention more closely than, a bare SHADOW_MT5 default would be.

NOT IMPLEMENTED / explicitly flagged as unresolved rather than guessed (per the user's own "stop
only where an exact mentor rule cannot be recovered" instruction):
  - Trendline liquidity (row 6/Order Flow notes): `pivot_trendlines.py` exists but is
    observability-only everywhere in this engine; not wired into any liquidity check here either.
  - "Clear residual liquidity first" (row 23, Order Block Liquidity): implemented as a soft
    evidence field only, not a hard gate, given the scope of this session.
  - Partial-profit-taking at intermediate FVGs (Under/Over, 9:30AM) and the mandated full-exit
    at TP (Asian Session): these are TRADE-MANAGEMENT rules, not entry/signal rules -- a
    stateless per-cycle evaluator cannot manage an already-open position. Captured as
    `evidence["management_style"]` metadata for Part 13's mentor-aware Adaptive Manager thesis to
    consume; not enforced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from backend.market_structure.bar_utils import StructureBar, normalize_bars
from backend.market_structure.models import (
    ImbalanceZone,
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweep,
    StructureBreak,
    StructureBreakKind,
    SwingPoint,
)
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_flag,
    _env_float,
    _geometry_metadata,
    _liquidity_sweep_precedes,
    _no_signal,
    _opposing_structural_level,
    _signal,
    _spread_within_safety_buffer,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "bsi"

_BULLISH = "bullish"
_BEARISH = "bearish"
_BUY_SIDE = LiquiditySide.BUY_SIDE.value
_SELL_SIDE = LiquiditySide.SELL_SIDE.value

# Row 2: mentor's MSS == Bensim's CHOCH-or-MSS (both, no displacement distinction in his own
# vocabulary). Used by every subtype EXCEPT ny_0930 (see module docstring point 1).
_MENTOR_MSS_KINDS = frozenset({StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value})
_MENTOR_ALL_BREAK_KINDS = frozenset({StructureBreakKind.BOS.value, StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value})

_NY_TZ = ZoneInfo("America/New_York")

# Candidate-generation starvation bug (2026-09-02, found while validating "no subtype is
# silently starved" per the post-Daily-bias-fix priority order): bsi_reactionary and
# bsi_ob_liquidity are real, registered evaluators (_SUBTYPE_EVALUATORS below) with their own
# BSI_SUBTYPE_ACTIVATION_* env vars (both ACTIVE_MT5 in production) -- but this tuple, which is
# the ONLY thing evaluate_bsi()'s dispatch loop iterates over, never listed them. Since
# evaluate_bsi_order_flow() defaults to entry_confirmation_mode="direct" and does not internally
# try the other two modes as a fallback, the two dedicated wrapper evaluators
# (evaluate_bsi_reactionary/evaluate_bsi_ob_liquidity) were simply never called: activated in
# config, coded, tested in isolation, but structurally unreachable from live/DEMO evaluation.
# Placed immediately after bsi_order_flow (their base pattern, per the module docstring's own
# "Order Flow... with more [twist]" framing) -- their entry preconditions are close to mutually
# exclusive with direct mode by construction (reactionary requires a SECOND, LATER array; direct
# requires price in the FIRST array right now), so this ordering choice rarely decides a
# collision, it mainly determines who gets first look on the rare cycle where more than one
# would otherwise validate.
_DEFAULT_SUBTYPE_ORDER = ("bsi_order_flow", "bsi_reactionary", "bsi_ob_liquidity", "bsi_abc", "bsi_asian", "bsi_new_york", "bsi_under_over", "bsi_0930", "bsi_abcd")


# ============================================================================================
# Shared primitives -- reused by every subtype below. None of these modify market_structure/*.py;
# each is new composition of already-detected snapshot objects (swings/breaks/liquidity/FVGs).
# ============================================================================================
def _bars(ctx: StrategyContext) -> list[StructureBar]:
    return normalize_bars(ctx.m15_rows, symbol=ctx.broker_symbol, timeframe="M15")


def _current_price(ctx: StrategyContext) -> float:
    return float(_closes(ctx.m15_rows).iloc[-1])


def _last_bar_time(ctx: StrategyContext) -> datetime:
    raw = ctx.m15_rows[-1].get("time") if ctx.m15_rows else None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return ctx.generated_at


def _trade_direction(structure_direction: str) -> str:
    return "LONG" if structure_direction == _BULLISH else "SHORT"


def _mentor_htf_direction(ctx: StrategyContext) -> str:
    """BSI Daily Bias Audit (2026-09-02, BSI_DAILY_BIAS_AUDIT.md): the mentor's own "high time
    frame market structure" reference is Daily, not H4 -- zero mentions of H4/4-hour anywhere in
    the 40-video course; "daily bias"/"daily time frame" used explicitly and repeatedly, including
    in a concrete real-trade walkthrough (9:30AM Example 3: "our daily bias, the market structure,
    the overall market structure was bearish"). ctx.htf_trend_daily (synthesized from H4 bars via
    backend/market_structure/daily_aggregation.py, since the live MT5-sourced candle table has no
    native D1 rows) is therefore preferred here over ctx.htf_trend_h4 for the 4 subtypes that gate
    on this value (bsi_order_flow, bsi_abc, bsi_0930, bsi_abcd, and their bsi_reactionary/
    bsi_ob_liquidity dependents via evaluate_bsi_order_flow).

    Falls back to ctx.htf_trend_h4 when ctx.htf_trend_daily is None -- deliberately, as a safety
    net, NOT a silent reversion of this fix: htf_trend_daily is None only when a caller hasn't yet
    threaded `daily_rows` through build_strategy_context (every live/replay call site this audit
    updated does). Without this fallback, any not-yet-updated caller (a test fixture, a research
    script, a future code path) would see these 4 subtypes permanently NO_HTF_BIAS-reject --
    functionally equivalent to the SHADOW demotion the user explicitly declined ("all 9 subtypes
    stay ACTIVE_MT5 throughout this work... do not change any subtype's activation state"), except
    happening silently as a bug rather than a deliberate, visible flag. This fallback exists
    specifically to prevent that outcome, not to hedge against the Daily-bias finding itself."""
    return ctx.htf_trend_daily if ctx.htf_trend_daily is not None else ctx.htf_trend_h4


def _latest_break(ctx: StrategyContext, *, direction: str, kinds: frozenset[str]) -> StructureBreak | None:
    candidates = [b for b in ctx.m15_snapshot.breaks if b.direction == direction and b.break_kind in kinds]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b.bar_index)


def _leg_bounds(ctx: StrategyContext, brk: StructureBreak) -> tuple[Decimal, Decimal, SwingPoint] | None:
    """Mentor-faithful Fibonacci/premium-discount anchor (row 4, module docstring point 2): the
    SPECIFIC impulsive/structure-breaking leg, start to end -- 'draw out your Fibonacci from the
    very top to the very low' of 'the move that broke the structure' (ENDGAME + Premium & Discount
    lessons, independently agreeing). leg_start = the last confirmed swing of the type OPPOSITE
    brk.direction before the break (the extreme the impulsive move began from -- for a bullish
    break this is the most recent swing LOW, mirrored for bearish); leg_end = the break's own
    confirmed break_price ('the very top/bottom of that push'). Returns the leg's (low, high,
    leg_start_swing) or None when no such swing exists yet."""
    opposite_type = "low" if brk.direction == _BULLISH else "high"
    candidates = [s for s in ctx.m15_snapshot.swings if s.swing_type == opposite_type and s.bar_index < brk.bar_index]
    if not candidates:
        return None
    leg_start = max(candidates, key=lambda s: s.bar_index)
    low, high = sorted((leg_start.price, brk.break_price))
    return low, high, leg_start


def _zone_favorable(direction: str, low: Decimal, high: Decimal, price: float) -> bool:
    """Premium/discount gate at exactly 0.5 (row 5, module docstring point 4): LONG only in
    discount (<=0.5 of the leg), SHORT only in premium (>=0.5) -- 'you don't want to just blindly
    sell off... there are certain criteria' (Premium & Discount lesson: the zone alone is a
    NECESSARY filter, never itself the entry trigger -- callers still need an entry array + a
    structural break, this function only answers the location question)."""
    if high <= low:
        return False
    midpoint = float((low + high) / 2)
    return price <= midpoint if direction == "LONG" else price >= midpoint


def _mentor_order_block_for_fvg(fvg: ImbalanceZone, bars: list[StructureBar]) -> tuple[Decimal, Decimal] | None:
    """Mentor-faithful order block (row 9, module docstring point 3): the FIRST candle of the
    SPECIFIC 3-candle sequence that forms THIS fvg -- reuses only detect_fair_value_gaps()'s own
    supporting_bar_indexes ([first, middle, third] for this exact gap, imbalance.py) plus the raw
    bar series. Deliberately does NOT call zones.py::detect_order_blocks() (break-anchored
    backward scan, the rule the mentor explicitly rejects as 'absolutely wrong')."""
    if not fvg.supporting_bar_indexes:
        return None
    origin_idx = fvg.supporting_bar_indexes[0]
    if origin_idx < 0 or origin_idx >= len(bars):
        return None
    origin = bars[origin_idx]
    return origin.low, origin.high


def _unmitigated_fvgs_in_zone(ctx: StrategyContext, direction: str, low: Decimal, high: Decimal) -> list[ImbalanceZone]:
    """FVG/OB must never be traded standalone-mitigated (row 8/10 + the Order Flow MSB example's
    explicit 'there's no imbalance here, it's all filled' rejection) -- ACTIVE or PARTIAL only,
    never MITIGATED, and must overlap the leg's own premium/discount zone (row 15's 'only
    consider the order block/imbalance in the [correct] zone')."""
    want_dir = _BULLISH if direction == "LONG" else _BEARISH
    out: list[ImbalanceZone] = []
    for z in ctx.m15_snapshot.imbalances:
        if z.direction != want_dir or z.status == "mitigated" or z.price_low is None or z.price_high is None:
            continue
        if z.price_high < low or z.price_low > high:
            continue
        out.append(z)
    return out


@dataclass(frozen=True)
class _EntryArray:
    kind: str  # "fvg" | "order_block"
    low: Decimal
    high: Decimal
    fvg_id: str | None
    size_atr: float | None
    ref_bar_index: int


def _select_entry_array(ctx: StrategyContext, direction: str, leg_low: Decimal, leg_high: Decimal, bars: list[StructureBar]) -> _EntryArray | None:
    """OB-vs-FVG entry choice (rows 9/11, module docstring point 5): small/tight FVG -> enter
    from the FVG directly ('I ALWAYS take my entry from the imbalance if the imbalance is very
    short'); large FVG -> enter from its own order block instead (better RR). When several
    qualify, prefer the MOST EXTREME (deepest into the zone, Order Flow's own stated refinement:
    'take your entry from the MOST EXTREME zone'). MT5_BSI_FVG_SIZE_ATR_THRESHOLD (default
    0.5 ATR) is Bensim's own numeric operationalization of the mentor's qualitative 'small vs
    big' rule -- no such number exists in the transcripts."""
    fvgs = _unmitigated_fvgs_in_zone(ctx, direction, leg_low, leg_high)
    if not fvgs:
        return None
    price = _current_price(ctx)

    def _extremity(z: ImbalanceZone) -> float:
        edge = float(z.price_low) if direction == "LONG" else float(z.price_high)
        return abs(price - edge)

    chosen_fvg = max(fvgs, key=_extremity)
    size_atr = chosen_fvg.strength
    ref_bar_index = chosen_fvg.supporting_bar_indexes[-1] if chosen_fvg.supporting_bar_indexes else 0
    threshold = _env_float("MT5_BSI_FVG_SIZE_ATR_THRESHOLD", 0.5)
    if size_atr is not None and size_atr <= threshold:
        return _EntryArray(kind="fvg", low=chosen_fvg.price_low, high=chosen_fvg.price_high, fvg_id=chosen_fvg.id, size_atr=size_atr, ref_bar_index=ref_bar_index)
    ob_bounds = _mentor_order_block_for_fvg(chosen_fvg, bars)
    if ob_bounds is None:
        return _EntryArray(kind="fvg", low=chosen_fvg.price_low, high=chosen_fvg.price_high, fvg_id=chosen_fvg.id, size_atr=size_atr, ref_bar_index=ref_bar_index)
    return _EntryArray(kind="order_block", low=ob_bounds[0], high=ob_bounds[1], fvg_id=chosen_fvg.id, size_atr=size_atr, ref_bar_index=ref_bar_index)


def _price_in_array(direction: str, array: _EntryArray, price: float) -> bool:
    low, high = min(array.low, array.high), max(array.low, array.high)
    return float(low) <= price <= float(high)


def _reactionary_confirmation(ctx: StrategyContext, direction: str, first_array: _EntryArray) -> _EntryArray | None:
    """Reactionary Block (Order Flow + a SAME-timeframe confirmation layer): after price
    touches the FIRST entry array, require a SECOND, fresh impulsive push away from it that
    creates its OWN new unmitigated FVG -- THAT second array is the actual entry. Mentor's own
    explicit qualifier: the second push needs NO fresh structural break of its own, purely a
    displacement/FVG-creation criterion."""
    want_dir = _BULLISH if direction == "LONG" else _BEARISH
    later = [
        z for z in ctx.m15_snapshot.imbalances
        if z.direction == want_dir and z.status != "mitigated" and z.supporting_bar_indexes
        and z.supporting_bar_indexes[0] > first_array.ref_bar_index
    ]
    if not later:
        return None
    second = max(later, key=lambda z: z.supporting_bar_indexes[0])
    ref_bar_index = second.supporting_bar_indexes[-1] if second.supporting_bar_indexes else first_array.ref_bar_index
    return _EntryArray(kind="fvg", low=second.price_low, high=second.price_high, fvg_id=second.id, size_atr=second.strength, ref_bar_index=ref_bar_index)


def _liquidity_twist_confirmation(direction: str, array: _EntryArray, bars: list[StructureBar]) -> tuple[bool, str]:
    """Order Block Liquidity's 'twist': the array must first be FAKED THROUGH (a later bar's
    CLOSE beyond its far edge -- 'wicks do not count', third independent confirmation of this
    rule) and then RECLAIMED, and the origin candle must be the single most extreme candle in its
    local sequence up to the fakeout (a later wick poking beyond it invalidates the setup
    outright -- both precise, directly-codeable rules from the Order Block Liquidity lesson)."""
    ref_idx = array.ref_bar_index
    far_edge = array.low if direction == "LONG" else array.high
    origin_extreme = far_edge
    fakeout_idx: int | None = None
    for i in range(ref_idx + 1, len(bars)):
        c = bars[i].close
        beyond = c < far_edge if direction == "LONG" else c > far_edge
        if beyond:
            fakeout_idx = i
            break
        wick_beyond = bars[i].low < origin_extreme if direction == "LONG" else bars[i].high > origin_extreme
        if wick_beyond:
            return False, "ORIGIN_CANDLE_NOT_LOCAL_EXTREMUM"
    if fakeout_idx is None:
        return False, "NO_FAKEOUT_THROUGH_ORDER_BLOCK"
    for j in range(fakeout_idx + 1, len(bars)):
        c = bars[j].close
        reclaimed = c > far_edge if direction == "LONG" else c < far_edge
        if reclaimed:
            return True, ""
    return False, "NO_RECLAIM_AFTER_FAKEOUT"


def _mentor_stop(ctx: StrategyContext, direction: str, entry: Decimal, array: _EntryArray, atr: Decimal, *, stop_mode: str, leg_low: Decimal, leg_high: Decimal) -> tuple[Decimal | None, str]:
    """SL = just beyond the entry-zone edge, opposite direction (row 13) -- 'tight' (default) uses
    the entry array's own edge; 'conservative' uses the larger structural leg boundary instead
    ('put your stop below this low... just to be on the safe side' -- a documented, repeatedly
    offered alternative, not a contradiction). min/max ATR bounds are Bensim's own bounding
    (no mentor-stated distance exists), matching every other strategy's own `_dynamic_stop`
    convention in this codebase."""
    structure_level = (leg_low if direction == "LONG" else leg_high) if stop_mode == "conservative" else (array.low if direction == "LONG" else array.high)
    return _dynamic_stop(ctx, direction, entry, structure_level, atr, min_atr_mult=0.3, max_atr_mult=5.0)


def _tp_fixed_rr(entry: Decimal, stop: Decimal, direction: str, rr: float) -> Decimal:
    risk = abs(entry - stop)
    return entry + risk * Decimal(str(rr)) if direction == "LONG" else entry - risk * Decimal(str(rr))


def _tp_bounded(entry: Decimal, stop: Decimal, direction: str, natural_target: Decimal | None, *, min_rr: float, max_rr: float) -> Decimal | None:
    """9:30AM's bounded [min_rr, max_rr] RR band (row 14): reject entirely below min_rr ('I go for
    MINIMUM risk reward 3'), clip to max_rr above it ('I can go for MAXIMUM 5... I do not chase
    big risk reward trades')."""
    risk = abs(entry - stop)
    if risk <= 0 or natural_target is None:
        return None
    implied_rr = float(abs(natural_target - entry) / risk)
    if implied_rr < min_rr:
        return None
    if implied_rr > max_rr:
        return entry + risk * Decimal(str(max_rr)) if direction == "LONG" else entry - risk * Decimal(str(max_rr))
    return natural_target


def _session_level(ctx: StrategyContext, session_name: str, level_name: str) -> Decimal | None:
    for lvl in ctx.m15_snapshot.session_levels:
        if lvl.session_name == session_name and lvl.level_name == level_name and lvl.level is not None:
            return lvl.level
    return None


def _in_lunch_window(dt: datetime) -> bool:
    """Asian Session's mandatory sweep-timing gate (row 19): the sweep must occur in the gap
    between Asian close and London open. Bensim's own configured session boxes
    (market_structure/configuration.py::SessionConfig, UTC): asian=00:00-06:00, london=07:00-
    10:00 -- the lunch gap is therefore 06:00-07:00 UTC. Only the SWEEP is time-gated; the
    confirming MSS/CHoCH is explicitly NOT ('it doesn't matter when price does a market structure
    shift... within that lunch period or during the London session')."""
    return dt.hour == 6


def _in_ny_session_window(dt: datetime) -> bool:
    """New York Session's mandatory sweep-timing gate: Bensim's own configured NY windows (UTC):
    new_york_morning=13:30-16:00, new_york_afternoon=18:00-20:00."""
    minutes = dt.hour * 60 + dt.minute
    return (13 * 60 + 30 <= minutes < 16 * 60) or (18 * 60 <= minutes < 20 * 60)


def _ny_local_minutes(dt_utc: datetime) -> int:
    local = dt_utc.astimezone(_NY_TZ)
    return local.hour * 60 + local.minute


def _in_930_window(dt_utc: datetime) -> bool:
    """9:30AM's hard, literal clock-time trading window: 'you can only trade between 9:30 AM till
    11:59 AM New York time' -- a real, confirmed Bensim infrastructure gap (no 9:30-specific
    concept exists anywhere in sessions.py), built new here rather than reusing a session box."""
    minutes = _ny_local_minutes(dt_utc)
    return 9 * 60 + 30 <= minutes <= 11 * 60 + 59


def _before_930(dt_utc: datetime) -> bool:
    return _ny_local_minutes(dt_utc) < 9 * 60 + 30


def _session_sweep_direction(ctx: StrategyContext, session_name: str, window_ok) -> tuple[str, int] | None:
    """Freshest qualifying sweep of `session_name`'s OWN high or low, gated to `window_ok(dt)` --
    Asian Session's specific mechanic (row 19/25): sweep the session's own box, not a generic
    swing. Uses the SAME wick-beyond/close-back-through sweep definition liquidity.py's own
    detect_liquidity_sweeps already uses, applied here to the session-level price rather than a
    swing-derived LiquidityLevel (session_levels are a separate concept/list in the snapshot, so
    this is new composition, not new detection). Sweeping the session HIGH traps buy-side
    breakout longs -> resulting reversal is BEARISH; sweeping the LOW -> BULLISH (identical
    convention to _opposing_structural_level's own 'ahead side' logic elsewhere in this
    codebase)."""
    bars = _bars(ctx)
    high = _session_level(ctx, session_name, "high")
    low = _session_level(ctx, session_name, "low")
    found: list[tuple[str, int]] = []
    if high is not None:
        for i, b in enumerate(bars):
            if b.high > high and b.close < high and window_ok(b.close_time):
                found.append((_BEARISH, i))
    if low is not None:
        for i, b in enumerate(bars):
            if b.low < low and b.close > low and window_ok(b.close_time):
                found.append((_BULLISH, i))
    if not found:
        return None
    return max(found, key=lambda t: t[1])


def _swing_sweep_in_window(ctx: StrategyContext, window_ok) -> LiquiditySweep | None:
    """A swing-derived (non-session) liquidity sweep whose confirming bar falls inside
    `window_ok` -- New York Session's mechanic: a 'key high'/'key low' (an ordinary confirmed
    swing, not a session box) gets swept during the NY window; the impulse+pullback legs
    themselves may have formed earlier ('you can have one leg starting from the London session')."""
    candidates = [s for s in ctx.m15_snapshot.liquidity_sweeps if s.confirmation_time and window_ok(s.confirmation_time)]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.bar_index)


def _mentor_equal_levels(ctx: StrategyContext, *, min_touches: int) -> list[LiquidityLevel]:
    """Under/Over's explicit >=3-touch requirement (row 6, doubly confirmed numeric rule) --
    Bensim's engine default (`EqualLevelConfig.minimum_touches=2`) is filtered UP to this
    subtype's own stricter bar rather than changed globally. Dedupes to the highest touch_count
    already emitted per price cluster (detect_equal_levels emits one row per touch-count
    milestone for the same cluster)."""
    best: dict[str, LiquidityLevel] = {}
    for lvl in ctx.m15_snapshot.equal_levels:
        if lvl.touch_count < min_touches:
            continue
        key = f"{lvl.side}:{round(float(lvl.level), 5)}"
        prev = best.get(key)
        if prev is None or lvl.touch_count > prev.touch_count:
            best[key] = lvl
    return list(best.values())


def _close_based_fakeout_reclaim(bars: list[StructureBar], level: Decimal, side: str, *, lookback_bars: int) -> tuple[int, int] | None:
    """Under/Over's explicit CLOSE-based (not wick) break-then-reclaim rule ('wicks do not
    matter, you just want to wait for the candle to close') -- deliberately distinct from
    liquidity.py's own WICK-beyond sweep definition, which this subtype does not use."""
    window = bars[-lookback_bars:] if lookback_bars < len(bars) else bars
    offset = len(bars) - len(window)
    fakeout_idx: int | None = None
    for i, b in enumerate(window):
        beyond = b.close < level if side == _SELL_SIDE else b.close > level
        if beyond:
            fakeout_idx = offset + i
    if fakeout_idx is None:
        return None
    for j in range(fakeout_idx + 1, len(bars)):
        reclaimed = bars[j].close > level if side == _SELL_SIDE else bars[j].close < level
        if reclaimed:
            return fakeout_idx, j
    return None


def _fakeout_quality_score(penetration_atr: float | None) -> float:
    """Fakeout-size soft quality filter (rows 21/NY-Example-4/Under-Over/Order-Block-Liquidity --
    a REPEATED, cross-strategy but explicitly fuzzy/discretionary rule: 'prefer small fakeouts',
    never a hard numeric cutoff, per the mentor's own inconsistent tolerance ('this fakeout is a
    bit big to my liking but it still works' vs 'this one has a huge fakeout, this doesn't
    count'). Scored, not gated -- MT5_BSI_MAX_FAKEOUT_ATR (default 3.0, generous) is the
    one hard-reject ceiling this module applies, for truly extreme cases only."""
    if penetration_atr is None:
        return 50.0
    if penetration_atr <= 0.5:
        return 100.0
    if penetration_atr >= 3.0:
        return 0.0
    return max(0.0, 100.0 - (penetration_atr - 0.5) * 40.0)


BSI_VERSION = "BSI_BASELINE_V1"


def _bsi_thesis_metadata(
    *, subtype: str, direction: str, structure_break_id: str | None, leg_low: Decimal, leg_high: Decimal,
    array: _EntryArray | None, entry: Decimal, stop: Decimal | None, target: Decimal | None,
    session: str | None, management_style: str, source_rule_ids: tuple[str, ...],
    external_structure: str | None = None, internal_structure: str | None = None,
    structure_break_level: Decimal | None = None, mss_level: Decimal | None = None,
    liquidity_source: str | None = None, liquidity_side: str | None = None,
    liquidity_swept: bool | None = None, sweep_time: datetime | None = None,
    session_window: str | None = None, entry_trigger: str | None = None, target_type: str | None = None,
) -> dict[str, Any]:
    """Section 7 persistence -- everything a BSI trade needs to be RECONSTRUCTABLE after the fact,
    not merely a confidence number (mission directive Part 7's own explicit field list). Every
    field not applicable to a given subtype/call is left None rather than fabricated (e.g.
    external_structure is None for asian_session/new_york_session/under_over, which the course
    explicitly does not gate on HTF bias -- row 17).

    'structure_direction' is derived here (bullish<->LONG, bearish<->SHORT) rather than accepted
    as a separate caller-supplied param -- this fixes a real latent bug in the prior mentor_smc
    version of this function, which stored the *trade* direction ('LONG'/'SHORT') under the
    'structure_direction' key while bsi_thesis.py::thesis_still_intact() compares that same key
    against 'bullish'/'bearish'. Neither string ever matched, so thesis_still_intact() silently
    fell through to its fail-open branch (`return True`) for every real signal this module ever
    produced -- the thesis-invalidation check was dead code end-to-end despite passing its own
    unit tests (those tests pass 'bullish'/'bearish' directly, bypassing this function entirely).
    Both 'direction' (LONG/SHORT, the tradeable side) and 'structure_direction' (bullish/bearish,
    the mentor's own vocabulary) are now persisted as genuinely distinct, correctly-populated
    fields, exactly as Section 7's field list asks for both."""
    structure_direction = "bullish" if direction == "LONG" else "bearish"
    equilibrium = float((leg_low + leg_high) / 2) if leg_high > leg_low else None
    entry_f = float(entry)
    premium_discount_location = None
    if equilibrium is not None:
        premium_discount_location = "premium" if entry_f >= equilibrium else "discount"
    stop_f = float(stop) if stop is not None else None
    target_f = float(target) if target is not None else None
    expected_rr = None
    if stop_f is not None and target_f is not None and stop_f != entry_f:
        expected_rr = round(abs(target_f - entry_f) / abs(entry_f - stop_f), 4)
    order_block_id = f"{array.fvg_id}_OB" if array is not None and array.kind == "order_block" and array.fvg_id else None
    order_block_bounds = (float(array.low), float(array.high)) if array is not None and array.kind == "order_block" else None
    fvg_id = array.fvg_id if array is not None and array.kind == "fvg" else (array.fvg_id if array is not None else None)
    fvg_bounds = (float(array.low), float(array.high)) if array is not None and array.kind == "fvg" else None
    return {
        "bsi_thesis": {
            "bsi_version": BSI_VERSION,
            "setup_subtype": subtype,
            "direction": direction,
            "structure_direction": structure_direction,
            "external_structure": external_structure,
            "internal_structure": internal_structure,
            "protected_high": float(leg_high),
            "protected_low": float(leg_low),
            "structure_break_level": float(structure_break_level) if structure_break_level is not None else None,
            "mss_level": float(mss_level) if mss_level is not None else None,
            "liquidity_source": liquidity_source,
            "liquidity_side": liquidity_side,
            "liquidity_level": target_f,
            "liquidity_swept": liquidity_swept,
            "sweep_time": sweep_time.isoformat() if sweep_time is not None else None,
            "dealing_range_low": float(leg_low),
            "dealing_range_high": float(leg_high),
            "equilibrium": equilibrium,
            "premium_discount_location": premium_discount_location,
            "fvg_id": fvg_id,
            "fvg_bounds": fvg_bounds,
            "order_block_id": order_block_id,
            "order_block_bounds": order_block_bounds,
            "session": session,
            "session_window": session_window,
            "entry_trigger": entry_trigger,
            "entry_price": entry_f,
            "structural_invalidation": float(leg_low) if direction == "LONG" else float(leg_high),
            "stop_price": stop_f,
            "target_type": target_type,
            "target_level": target_f,
            "expected_rr": expected_rr,
            "source_rule_ids": list(source_rule_ids),
            # Legacy aliases kept for the not-yet-rewired bsi_thesis.py/bsi_confidence.py reference
            # modules (Part 12/13), which still read 'protected_swing'/'management_style' under
            # their original names -- both values are also available under their Section-7 names
            # above; these are not new information, just back-compat keys for the reference design
            # modules until they're updated to read the Section-7 names directly.
            "protected_swing": float(leg_low if direction == "LONG" else leg_high),
            "management_style": management_style,
        }
    }


# ============================================================================================
# order_flow -- the base framework: HTF bias -> qualifying M15 break (with-HTF-trend, BOS or
# CHoCH/MSS) -> leg-anchored Fibonacci/premium-discount -> unmitigated FVG/OB entry array ->
# opposing-liquidity TP. entry_confirmation_mode layers Reactionary Block / Order Block Liquidity
# on top (module docstring: both are self-described "Order Flow + a twist", not independent
# shapes).
# ============================================================================================
def evaluate_bsi_order_flow(ctx: StrategyContext, *, entry_confirmation_mode: str = "direct", stop_mode: str = "tight") -> StrategySignal:
    tf = "H4->M15"
    strategy_id = _STRATEGY_ID if entry_confirmation_mode == "direct" else f"{_STRATEGY_ID}_{entry_confirmation_mode}"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    htf_direction = _mentor_htf_direction(ctx)
    if htf_direction not in (_BULLISH, _BEARISH):
        return _reject("NO_HTF_BIAS")
    direction = _trade_direction(htf_direction)
    brk = _latest_break(ctx, direction=htf_direction, kinds=_MENTOR_ALL_BREAK_KINDS)
    if brk is None:
        return _reject("NO_QUALIFYING_STRUCTURE_BREAK")
    leg = _leg_bounds(ctx, brk)
    if leg is None:
        return _reject("NO_LEG_FOR_FIBONACCI_ANCHOR")
    leg_low, leg_high, _leg_start = leg
    price = _current_price(ctx)
    if not _zone_favorable(direction, leg_low, leg_high, price):
        return _reject("NOT_IN_PREMIUM_DISCOUNT_ZONE")
    bars = _bars(ctx)
    array = _select_entry_array(ctx, direction, leg_low, leg_high, bars)
    if array is None:
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    # BSI Mentor Audit fix (2026-09-02): the array-1 "price must be sitting in it right now" gate
    # below is correct for direct/ob_liquidity modes (array 1 IS the entry zone for those), but was
    # a genuine bug for reactionary mode -- it silently made bsi_reactionary near-impossible to
    # fire. Reactionary's own mechanic (mentor_notes_reactionary_block.md) requires a SECOND, LATER
    # array formed by a fresh impulsive push AWAY FROM array 1; by construction that second array
    # sits on the opposite side of array 1 from where price started, so requiring the SAME current-
    # price snapshot to simultaneously satisfy "inside array 1" (this check) AND, moments later,
    # "inside array 2" (the reactionary-mode check further below) is a self-contradictory
    # precondition -- array 1 is only a historical anchor/precursor for this mode, never itself the
    # entry trigger, so it must not be gated on current price. direct/ob_liquidity are unaffected:
    # array 1 remains their real entry zone and this check still applies to them unchanged.
    if entry_confirmation_mode != "reactionary" and not _price_in_array(direction, array, price):
        return _reject("PRICE_NOT_IN_ENTRY_ZONE")

    if entry_confirmation_mode == "reactionary":
        confirmed = _reactionary_confirmation(ctx, direction, array)
        if confirmed is None:
            return _reject("NO_REACTIONARY_CONFIRMATION")
        array = confirmed
        if not _price_in_array(direction, array, price):
            return _reject("PRICE_NOT_IN_REACTIONARY_ZONE")
    elif entry_confirmation_mode == "ob_liquidity":
        ok, reason = _liquidity_twist_confirmation(direction, array, bars)
        if not ok:
            return _reject(reason)

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode=stop_mode, leg_low=leg_low, leg_high=leg_high)
    if stop is None:
        return _reject(stop_reason)
    target = _opposing_structural_level(ctx, direction)
    if target is None:
        return _reject("NO_OPPOSING_LIQUIDITY_TARGET")

    sweep_precedes = _liquidity_sweep_precedes(ctx, before_bar_index=brk.bar_index, trend_direction=brk.direction)
    strength = 60.0 + (15.0 if array.kind == "order_block" else 8.0) + (10.0 if sweep_precedes else 0.0) + (10.0 if entry_confirmation_mode != "direct" else 0.0)
    evidence = {
        "setup_subtype": "bsi_order_flow" if entry_confirmation_mode == "direct" else strategy_id,
        "entry_confirmation_mode": entry_confirmation_mode,
        "htf_bias": htf_direction,
        "structure_break_id": brk.id,
        "structure_break_kind": brk.break_kind,
        "leg_low": float(leg_low),
        "leg_high": float(leg_high),
        "entry_array_kind": array.kind,
        "entry_array_fvg_id": array.fvg_id,
        "entry_array_size_atr": array.size_atr,
        "liquidity_sweep_precedes_break": sweep_precedes,
        "stop_mode": stop_mode,
    }
    metadata = _geometry_metadata(ctx, entry, stop, array.low if direction == "LONG" else array.high, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype=evidence["setup_subtype"], direction=direction, structure_break_id=brk.id, leg_low=leg_low, leg_high=leg_high,
        array=array, entry=entry, stop=stop, target=target, session=None, management_style="natural_rr_no_partials",
        external_structure=htf_direction, structure_break_level=brk.broken_level,
        mss_level=brk.break_price if brk.break_kind in _MENTOR_MSS_KINDS else None,
        liquidity_source="opposing_structural_level", liquidity_side=_BUY_SIDE if direction == "SHORT" else _SELL_SIDE,
        liquidity_swept=sweep_precedes, entry_trigger=f"qualifying_structure_break_plus_entry_array[{entry_confirmation_mode}]",
        target_type="natural_opposing_liquidity",
        source_rule_ids=("row_1", "row_2", "row_4", "row_5", "row_7", "row_8", "row_9", "row_11", "row_13", "row_14"),
    ))
    return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


def evaluate_bsi_reactionary(ctx: StrategyContext) -> StrategySignal:
    return evaluate_bsi_order_flow(ctx, entry_confirmation_mode="reactionary")


def evaluate_bsi_ob_liquidity(ctx: StrategyContext) -> StrategySignal:
    return evaluate_bsi_order_flow(ctx, entry_confirmation_mode="ob_liquidity")


# ============================================================================================
# abc -- A/B/C leg geometry + location-scoped internal structure (row 3, the clearest answer in
# the whole course to "a tiny internal swing must not count"). See _find_abc_legs for the exact
# geometric reconstruction and its reasoning (documented there since it required resolving a
# genuine ambiguity in the transcript about which level TP anchors to).
# ============================================================================================
def _find_abc_legs(ctx: StrategyContext, direction: str) -> tuple[SwingPoint, SwingPoint, SwingPoint] | None:
    """P0 (A-leg start) -> P1 (A-leg end / B-leg start) -> P2 (B-leg end / C-leg start), the most
    recent confirmed alternating swing triple. Invalidation (mentor's own precise words): 'the
    high of the B leg must NOT EXCEED the A leg level -- if the B leg crosses this level, the
    trade setup becomes invalid' -- i.e. P2 must not retrace back past P0."""
    p2_type = "low" if direction == "LONG" else "high"
    p1_type = "high" if direction == "LONG" else "low"
    p0_type = p2_type
    ordered = sorted(ctx.m15_snapshot.swings, key=lambda s: s.bar_index)
    p2 = next((s for s in reversed(ordered) if s.swing_type == p2_type), None)
    if p2 is None:
        return None
    p1 = next((s for s in reversed(ordered) if s.swing_type == p1_type and s.bar_index < p2.bar_index), None)
    if p1 is None:
        return None
    p0 = next((s for s in reversed(ordered) if s.swing_type == p0_type and s.bar_index < p1.bar_index), None)
    if p0 is None:
        return None
    if direction == "LONG" and p2.price <= p0.price:
        return None
    if direction == "SHORT" and p2.price >= p0.price:
        return None
    return p0, p1, p2


def _abc_internal_break(ctx: StrategyContext, direction: str, p1: SwingPoint, p2: SwingPoint) -> StructureBreak | None:
    """'When the price is about to break the B leg level, it MUST create some structure... we are
    ONLY going to look for structure which is INSIDE the B leg and the C leg' -- a structural
    break located elsewhere on the chart explicitly 'does not count' for this setup. Scoped here
    to breaks whose OWN broken_level sits within [P2, P1] and whose bar occurs after P2."""
    zone_low, zone_high = sorted((p1.price, p2.price))
    want_dir = _BULLISH if direction == "LONG" else _BEARISH
    candidates = [b for b in ctx.m15_snapshot.breaks if b.direction == want_dir and b.bar_index > p2.bar_index and zone_low <= b.broken_level <= zone_high]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b.bar_index)


def _p1_already_broken(ctx: StrategyContext, p1: SwingPoint) -> bool:
    """Sequencing invalidation (matches NY Session Example 4's identical rule): if price already
    fully broke P1 (the B-leg/A-leg boundary -- ABC's own TP anchor, see docstring below) before
    our entry sequence completed, the trade already ran to target without us -- 'the price did
    not take out our take profit area, so this trade is now valid' implies the mirror failure."""
    return any(b.broken_swing_id == p1.id for b in ctx.m15_snapshot.breaks)


def evaluate_bsi_abc(ctx: StrategyContext) -> StrategySignal:
    """TP anchor reasoning (a genuine transcript ambiguity resolved by internal consistency, not
    guessed): the mentor states TP = 'the high of the B leg', which is P1 (B leg's own start,
    equal to A leg's own extreme). This only makes geometric sense as a target BEYOND entry if
    entry is taken EARLY in the C leg -- from the OB/FVG of the internal structural break that
    forms INSIDE the [P2, P1] zone, before price has fully reclaimed P1 -- rather than after C has
    already broken out past P1. That reading is what is implemented here: entry only fires while
    P1 remains unbroken (see _p1_already_broken), so P1 is still strictly ahead of price in the
    trade direction; if this reading is wrong for some geometries, the shared `_signal()`
    geometry validator (INVALID_TARGET_DIRECTION) rejects the signal rather than silently forcing
    a fabricated target -- fail-closed on the ambiguity rather than inventing a substitute rule.
    """
    tf = "H4->M15(ABC)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    htf_direction = _mentor_htf_direction(ctx)
    if htf_direction not in (_BULLISH, _BEARISH):
        return _reject("NO_HTF_BIAS")
    direction = _trade_direction(htf_direction)
    legs = _find_abc_legs(ctx, direction)
    if legs is None:
        return _reject("NO_VALID_ABC_LEGS")
    p0, p1, p2 = legs
    if _p1_already_broken(ctx, p1):
        return _reject("B_LEG_ALREADY_BROKEN_CHASE_INVALID")
    internal_break = _abc_internal_break(ctx, direction, p1, p2)
    if internal_break is None:
        return _reject("NO_INTERNAL_STRUCTURE_IN_BC_ZONE")
    bars = _bars(ctx)
    zone_low, zone_high = sorted((p1.price, p2.price))
    array = _select_entry_array(ctx, direction, zone_low, zone_high, bars)
    if array is None:
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    price = _current_price(ctx)
    if not _price_in_array(direction, array, price):
        return _reject("PRICE_NOT_IN_ENTRY_ZONE")

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    leg_low, leg_high = sorted((p0.price, p1.price))
    stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode="tight", leg_low=leg_low, leg_high=leg_high)
    if stop is None:
        return _reject(stop_reason)
    target = p1.price

    evidence = {
        "setup_subtype": "bsi_abc",
        "htf_bias": htf_direction,
        "p0_price": float(p0.price), "p1_price": float(p1.price), "p2_price": float(p2.price),
        "internal_break_id": internal_break.id,
        "entry_array_kind": array.kind,
        "entry_array_fvg_id": array.fvg_id,
    }
    metadata = _geometry_metadata(ctx, entry, stop, array.low if direction == "LONG" else array.high, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_abc", direction=direction, structure_break_id=internal_break.id, leg_low=leg_low, leg_high=leg_high,
        array=array, entry=entry, stop=stop, target=target, session=None, management_style="natural_target_b_leg",
        external_structure=htf_direction, internal_structure=internal_break.break_kind,
        structure_break_level=internal_break.broken_level, liquidity_source="b_leg_extreme_p1",
        liquidity_side=None, entry_trigger="internal_break_inside_bc_zone_before_p1_reclaim",
        target_type="b_leg_extreme",
        source_rule_ids=("row_3", "row_4", "row_5", "row_8", "row_9", "row_14", "row_16"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=75.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# asian_session -- no HTF bias required (mentor's own explicit "it's an algorithmic trading
# strategy, no daily bias needed"). Sweep must occur in the lunch gap; MSS confirmation is not
# time-gated; TP = the opposite side of the SAME session box; full exit at TP (management note).
# ============================================================================================
def evaluate_bsi_asian(ctx: StrategyContext) -> StrategySignal:
    tf = "M15(bsi_asian)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    sweep = _session_sweep_direction(ctx, "asian", _in_lunch_window)
    if sweep is None:
        return _reject("NO_ASIAN_LUNCH_WINDOW_SWEEP")
    structure_direction, sweep_bar_index = sweep
    mss = _latest_break(ctx, direction=structure_direction, kinds=_MENTOR_MSS_KINDS)
    if mss is None or mss.bar_index < sweep_bar_index:
        return _reject("NO_MSS_AFTER_SWEEP")
    direction = _trade_direction(structure_direction)
    leg = _leg_bounds(ctx, mss)
    if leg is None:
        return _reject("NO_LEG_FOR_FIBONACCI_ANCHOR")
    leg_low, leg_high, _leg_start = leg
    bars = _bars(ctx)
    array = _select_entry_array(ctx, direction, leg_low, leg_high, bars)
    if array is None:
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    price = _current_price(ctx)
    if not _price_in_array(direction, array, price):
        return _reject("PRICE_NOT_IN_ENTRY_ZONE")

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode="tight", leg_low=leg_low, leg_high=leg_high)
    if stop is None:
        return _reject(stop_reason)
    opposite_level_name = "low" if structure_direction == _BEARISH else "high"
    target = _session_level(ctx, "asian", opposite_level_name)
    if target is None:
        return _reject("NO_OPPOSITE_SESSION_LEVEL")

    evidence = {
        "setup_subtype": "bsi_asian",
        "htf_bias_required": False,
        "sweep_bar_index": sweep_bar_index,
        "structure_break_id": mss.id,
        "structure_break_kind": mss.break_kind,
        "entry_array_kind": array.kind,
        "entry_array_fvg_id": array.fvg_id,
    }
    metadata = _geometry_metadata(ctx, entry, stop, array.low if direction == "LONG" else array.high, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_asian", direction=direction, structure_break_id=mss.id, leg_low=leg_low, leg_high=leg_high,
        array=array, entry=entry, stop=stop, target=target, session="asian", management_style="full_exit_at_tp",
        mss_level=mss.break_price, liquidity_source="asian_session_box", liquidity_side=_SELL_SIDE if structure_direction == _BEARISH else _BUY_SIDE,
        liquidity_swept=True, sweep_time=bars[sweep_bar_index].close_time if sweep_bar_index < len(bars) else None,
        session_window="asian_lunch_gap_06:00-07:00_UTC", entry_trigger="mss_after_asian_session_box_sweep_in_lunch_window",
        target_type="opposite_asian_session_level",
        source_rule_ids=("row_2", "row_4", "row_5", "row_6", "row_8", "row_9", "row_17", "row_19"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=72.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# new_york_session -- a swing-derived (not session-box) sweep occurring in the NY window,
# retest-only entry (no OB/FVG required per the mentor's own description), fixed 1:2 RR.
# ============================================================================================
def evaluate_bsi_new_york(ctx: StrategyContext) -> StrategySignal:
    tf = "M15(bsi_new_york)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    sweep = _swing_sweep_in_window(ctx, _in_ny_session_window)
    if sweep is None:
        return _reject("NO_SWING_SWEEP_IN_NY_WINDOW")
    # BUY_SIDE swept (a high) -> reversal SHORT; SELL_SIDE swept (a low) -> reversal LONG (same
    # 'ahead side' convention as _opposing_structural_level elsewhere in this codebase).
    direction = "SHORT" if sweep.side == _BUY_SIDE else "LONG"
    atr_f = float(ctx.atr_m15) if ctx.atr_m15 else None
    penetration_atr = float(sweep.strength) if sweep.strength is not None else None
    max_fakeout_atr = _env_float("MT5_BSI_MAX_FAKEOUT_ATR", 3.0)
    if penetration_atr is not None and penetration_atr > max_fakeout_atr:
        return _reject("FAKEOUT_TOO_LARGE")
    price = _current_price(ctx)
    swept_level = float(sweep.swept_price)
    retest_tolerance_atr = _env_float("MT5_BSI_RETEST_TOLERANCE_ATR", 0.3)
    tolerance = (atr_f or 0.0005) * retest_tolerance_atr
    if abs(price - swept_level) > tolerance:
        return _reject("PRICE_NOT_AT_RETEST_LEVEL")

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, sweep.swept_price, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
    if stop is None:
        return _reject(stop_reason)
    target = _tp_fixed_rr(entry, stop, direction, 2.0)

    evidence = {
        "setup_subtype": "bsi_new_york",
        "sweep_id": sweep.id,
        "sweep_side": sweep.side,
        "penetration_atr": penetration_atr,
        "fakeout_quality_score": _fakeout_quality_score(penetration_atr),
        "fixed_rr": 2.0,
    }
    metadata = _geometry_metadata(ctx, entry, stop, sweep.swept_price, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_new_york", direction=direction, structure_break_id=None,
        leg_low=min(sweep.swept_price, entry), leg_high=max(sweep.swept_price, entry),
        array=None, entry=entry, stop=stop, target=target, session="new_york", management_style="fixed_1_2_rr_full_exit",
        liquidity_source="swing_sweep_in_ny_window", liquidity_side=sweep.side, liquidity_swept=True,
        sweep_time=sweep.confirmation_time, session_window="new_york_morning_13:30-16:00_UTC_or_afternoon_18:00-20:00_UTC",
        entry_trigger="retest_of_swept_swing_level_no_ob_fvg_required", target_type="fixed_1_2_rr",
        source_rule_ids=("row_2", "row_6", "row_14", "row_19", "row_21"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=68.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# under_over -- >=3-touch equal level, CLOSE-based fakeout+reclaim (wicks never count), natural
# opposing-liquidity TP with a documented (not enforced) partial-exit management style.
# ============================================================================================
def evaluate_bsi_under_over(ctx: StrategyContext) -> StrategySignal:
    tf = "M15(bsi_under_over)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    min_touches = int(_env_float("MT5_BSI_UNDER_OVER_MIN_TOUCHES", 3))
    levels = _mentor_equal_levels(ctx, min_touches=min_touches)
    if not levels:
        return _reject("NO_QUALIFYING_MULTI_TOUCH_LEVEL")
    bars = _bars(ctx)
    lookback = min(len(bars), 60)
    best: tuple[LiquidityLevel, tuple[int, int]] | None = None
    for lvl in levels:
        result = _close_based_fakeout_reclaim(bars, lvl.level, lvl.side, lookback_bars=lookback)
        if result is not None and (best is None or result[1] > best[1][1]):
            best = (lvl, result)
    if best is None:
        return _reject("NO_CLOSE_BASED_FAKEOUT_RECLAIM")
    level, (fakeout_idx, reclaim_idx) = best
    direction = "LONG" if level.side == _SELL_SIDE else "SHORT"
    price = _current_price(ctx)
    atr_f = float(ctx.atr_m15) if ctx.atr_m15 else None
    tolerance = (atr_f or 0.0005) * _env_float("MT5_BSI_RETEST_TOLERANCE_ATR", 0.3)
    if abs(price - float(level.level)) > tolerance:
        return _reject("PRICE_NOT_AT_RECLAIM_LEVEL")

    fakeout_extreme = bars[fakeout_idx].low if direction == "LONG" else bars[fakeout_idx].high
    penetration_atr = (abs(float(fakeout_extreme) - float(level.level)) / atr_f) if atr_f else None
    max_fakeout_atr = _env_float("MT5_BSI_MAX_FAKEOUT_ATR", 3.0)
    if penetration_atr is not None and penetration_atr > max_fakeout_atr:
        return _reject("FAKEOUT_TOO_LARGE")

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, fakeout_extreme, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
    if stop is None:
        return _reject(stop_reason)
    target = _opposing_structural_level(ctx, direction)
    if target is None:
        return _reject("NO_OPPOSING_LIQUIDITY_TARGET")

    evidence = {
        "setup_subtype": "bsi_under_over",
        "level_id": level.id,
        "level_touch_count": level.touch_count,
        "level_side": level.side,
        "penetration_atr": penetration_atr,
        "fakeout_quality_score": _fakeout_quality_score(penetration_atr),
    }
    metadata = _geometry_metadata(ctx, entry, stop, fakeout_extreme, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_under_over", direction=direction, structure_break_id=None,
        leg_low=min(level.level, entry), leg_high=max(level.level, entry),
        array=None, entry=entry, stop=stop, target=target, session=None, management_style="partial_at_intermediate_fvgs_full_at_target",
        liquidity_source="equal_level_min_3_touches", liquidity_side=level.side, liquidity_swept=True,
        sweep_time=bars[fakeout_idx].close_time if fakeout_idx < len(bars) else None,
        entry_trigger="close_based_fakeout_then_reclaim_of_multi_touch_level", target_type="natural_opposing_liquidity",
        source_rule_ids=("row_6", "row_9", "row_14", "row_21", "row_24"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=70.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# ny_0930 -- literal NY-local 9:30-11:59 clock window, HTF bias required, Bensim's STRICT
# (displacement-gated) MSS only [or a strong-displacement substitute per Example 3's nuance],
# bounded [3R, 5R] target.
# ============================================================================================
def evaluate_bsi_0930(ctx: StrategyContext) -> StrategySignal:
    tf = "15m->1m(bsi_0930)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    now = _last_bar_time(ctx)
    if not _in_930_window(now):
        return _reject("OUTSIDE_930_1159_NY_WINDOW")
    htf_direction = _mentor_htf_direction(ctx)
    if htf_direction not in (_BULLISH, _BEARISH):
        return _reject("NO_HTF_BIAS")
    direction = _trade_direction(htf_direction)

    strict_mss = _latest_break(ctx, direction=htf_direction, kinds=frozenset({StructureBreakKind.MSS.value}))
    strong_displacement = any(
        d.direction == htf_direction and (d.magnitude_atr or 0.0) >= _env_float("MT5_BSI_930_STRONG_DISPLACEMENT_ATR", 2.0)
        for d in ctx.m15_snapshot.displacements
    )
    if strict_mss is None and not strong_displacement:
        return _reject("NO_MSS_OR_STRONG_DISPLACEMENT")

    reference_break = strict_mss or _latest_break(ctx, direction=htf_direction, kinds=_MENTOR_ALL_BREAK_KINDS)
    if reference_break is None:
        return _reject("NO_QUALIFYING_STRUCTURE_BREAK")
    leg = _leg_bounds(ctx, reference_break)
    if leg is None:
        return _reject("NO_LEG_FOR_FIBONACCI_ANCHOR")
    leg_low, leg_high, _leg_start = leg
    bars = _bars(ctx)
    array = _select_entry_array(ctx, direction, leg_low, leg_high, bars)
    if array is None:
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    price = _current_price(ctx)
    if not _price_in_array(direction, array, price):
        return _reject("PRICE_NOT_IN_ENTRY_ZONE")

    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode="tight", leg_low=leg_low, leg_high=leg_high)
    if stop is None:
        return _reject(stop_reason)
    natural_target = _opposing_structural_level(ctx, direction)
    target = _tp_bounded(entry, stop, direction, natural_target, min_rr=3.0, max_rr=5.0)
    if target is None:
        return _reject("RR_OUTSIDE_3_5_BAND")

    evidence = {
        "setup_subtype": "bsi_0930",
        "htf_bias": htf_direction,
        "strict_mss_used": strict_mss is not None,
        "strong_displacement_substitute_used": strict_mss is None and strong_displacement,
        "structure_break_id": reference_break.id,
        "entry_array_kind": array.kind,
        "entry_array_fvg_id": array.fvg_id,
    }
    metadata = _geometry_metadata(ctx, entry, stop, array.low if direction == "LONG" else array.high, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_0930", direction=direction, structure_break_id=reference_break.id, leg_low=leg_low, leg_high=leg_high,
        array=array, entry=entry, stop=stop, target=target, session="new_york", management_style="structure_break_breakeven_partial",
        external_structure=htf_direction, mss_level=reference_break.break_price if strict_mss is not None else None,
        structure_break_level=reference_break.broken_level, liquidity_source="opposing_structural_level",
        liquidity_side=_BUY_SIDE if direction == "SHORT" else _SELL_SIDE,
        session_window="09:30-11:59_America/New_York", entry_trigger="strict_mss_or_strong_displacement_in_930_window",
        target_type="bounded_3r_5r",
        source_rule_ids=("row_2", "row_4", "row_5", "row_8", "row_9", "row_14", "row_19", "row_20"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=78.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# abcd -- ABC completion (P1 already broken) + a New-York-Session-shaped D-leg (break P2, reclaim,
# retest entry), fixed 1:2 RR -- the mentor's own explicit "this IS a New York session trading
# strategy" composition.
# ============================================================================================
def evaluate_bsi_abcd(ctx: StrategyContext) -> StrategySignal:
    tf = "M15(bsi_abcd)"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    htf_direction = _mentor_htf_direction(ctx)
    if htf_direction not in (_BULLISH, _BEARISH):
        return _reject("NO_HTF_BIAS")
    # D-leg reverses C's direction, so D's trade direction is OPPOSITE the ABC's own direction.
    abc_direction = _trade_direction(htf_direction)
    legs = _find_abc_legs(ctx, abc_direction)
    if legs is None:
        return _reject("NO_VALID_ABC_LEGS")
    p0, p1, p2 = legs
    if not _p1_already_broken(ctx, p1):
        return _reject("ABC_NOT_YET_COMPLETED")
    bars = _bars(ctx)
    d_direction = "SHORT" if abc_direction == "LONG" else "LONG"
    lookback = min(len(bars), 60)
    d_side = _SELL_SIDE if d_direction == "LONG" else _BUY_SIDE
    result = _close_based_fakeout_reclaim(bars, p2.price, d_side, lookback_bars=lookback)
    if result is None:
        return _reject("NO_D_LEG_BREAK_AND_RECLAIM")
    fakeout_idx, _reclaim_idx = result
    price = _current_price(ctx)
    atr_f = float(ctx.atr_m15) if ctx.atr_m15 else None
    tolerance = (atr_f or 0.0005) * _env_float("MT5_BSI_RETEST_TOLERANCE_ATR", 0.3)
    if abs(price - float(p2.price)) > tolerance:
        return _reject("PRICE_NOT_AT_D_LEG_RETEST")

    d_leg_extreme = bars[fakeout_idx].low if d_direction == "LONG" else bars[fakeout_idx].high
    entry = Decimal(str(price))
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, d_direction, entry, d_leg_extreme, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
    if stop is None:
        return _reject(stop_reason)
    target = _tp_fixed_rr(entry, stop, d_direction, 2.0)

    evidence = {
        "setup_subtype": "bsi_abcd",
        "htf_bias": htf_direction,
        "p0_price": float(p0.price), "p1_price": float(p1.price), "p2_price": float(p2.price),
        "fixed_rr": 2.0,
    }
    metadata = _geometry_metadata(ctx, entry, stop, d_leg_extreme, atr_dec, 0.3, 5.0)
    metadata.update(_bsi_thesis_metadata(
        subtype="bsi_abcd", direction=d_direction, structure_break_id=None,
        leg_low=min(p2.price, entry), leg_high=max(p2.price, entry),
        array=None, entry=entry, stop=stop, target=target, session="new_york", management_style="fixed_1_2_rr_full_exit",
        external_structure=htf_direction, internal_structure="abc_completed_p1_broken",
        liquidity_source="d_leg_p2_break_and_reclaim", liquidity_side=d_side, liquidity_swept=True,
        sweep_time=bars[fakeout_idx].close_time if fakeout_idx < len(bars) else None,
        session_window="new_york_session", entry_trigger="d_leg_close_based_break_reclaim_retest_of_p2",
        target_type="fixed_1_2_rr",
        source_rule_ids=("row_3", "row_4", "row_14", "row_19"),
    ))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe=tf, direction=d_direction, strength=73.0,
                    entry=entry, stop=stop, target=target, evidence=evidence, metadata=metadata)


# ============================================================================================
# Top-level dispatcher -- the ONE registered EVALUATORS/STRATEGY_FAMILIES entry. Tries subtypes in
# order, returns the first valid signal.
# ============================================================================================
_SUBTYPE_EVALUATORS: dict[str, Any] = {
    "bsi_order_flow": evaluate_bsi_order_flow,
    "bsi_abc": evaluate_bsi_abc,
    "bsi_asian": evaluate_bsi_asian,
    "bsi_new_york": evaluate_bsi_new_york,
    "bsi_under_over": evaluate_bsi_under_over,
    "bsi_0930": evaluate_bsi_0930,
    "bsi_abcd": evaluate_bsi_abcd,
    "bsi_reactionary": evaluate_bsi_reactionary,
    "bsi_ob_liquidity": evaluate_bsi_ob_liquidity,
}


def evaluate_bsi_subtype(ctx: StrategyContext, subtype: str) -> StrategySignal:
    """Direct, single-subtype entry point for backtesting/component-attribution (Part 10/11) --
    callers that need to isolate exactly one subtype's own performance without the others
    contending for the same cycle should call this rather than `evaluate_bsi`."""
    evaluator = _SUBTYPE_EVALUATORS.get(subtype)
    if evaluator is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=f"UNKNOWN_SUBTYPE_{subtype}")
    return evaluator(ctx)


def evaluate_bsi(ctx: StrategyContext) -> StrategySignal:
    """The single registered bsi evaluator (STRATEGY_FAMILIES/EVALUATORS) -- tries each
    subtype in BSI_SUBTYPE_ORDER (env, comma-separated, default the mentor's own
    best-documented-first ordering) and returns the first one that fires a valid signal.

    BSI Intelligence Migration (2026-09-01): each subtype now passes through
    `models.bsi_subtype_activation_status()` BEFORE its evaluator is ever called -- a subtype at
    DISABLED (the default for all 9) is skipped entirely, never reaching candidate generation,
    exactly like a DISABLED strategy family is skipped in `families/__init__.py::evaluate_all()`.
    This is a SECOND, more granular gate NESTED UNDER that same family-level one (still DISABLED,
    unchanged, so this whole function is not even reached in live/DEMO evaluation today) -- see
    `models.py`'s own extensive comment at `bsi_subtype_activation_status()` for the full
    reasoning and the explicit SHADOW_MT5-vs-ACTIVE_MT5 execution-layer scope boundary. The
    resolved status is stamped onto the winning (or last-tried) signal's own evidence dict so a
    future execution-layer consumer can read it.

    BSI_CONFIDENCE_V1 floor (2026-09-01, evidence-derived per bsi_confidence_v1.py's own module
    docstring): computed and stamped onto evidence["bsi_confidence_v1_score"] for EVERY BSI
    subtype's valid signal, unconditionally (shadow-tracking data collection continues for all 9
    subtypes regardless of gate state -- this is intentional, not a gap). The FLOOR REJECTION
    itself is scoped exclusively to bsi_under_over (the only subtype with real, chronologically-
    checked calibration evidence behind it -- see BSI_CONFIDENCE_V1_GATED_SUBTYPE) and only takes
    effect when BSI_CONFIDENCE_V1_GATE_ENABLED=true (default false -- shipped inert). When a
    bsi_under_over signal is rejected by the floor, the loop correctly continues to the NEXT
    subtype in `order` (exactly the same "try the next one" behavior DISABLED subtypes already
    get), never silently stops the whole dispatch."""
    import os
    from dataclasses import replace as _dc_replace

    from backend.mt5_strategies.families.bsi_confidence_v1 import (
        BSI_CONFIDENCE_V1_FLOOR_DEFAULT,
        BSI_CONFIDENCE_V1_GATED_SUBTYPE,
        bsi_under_over_floor_verdict,
        score_live_signal,
    )
    from backend.mt5_strategies.models import DISABLED, bsi_subtype_activation_status

    order_raw = os.getenv("BSI_SUBTYPE_ORDER")
    order = tuple(s.strip() for s in order_raw.split(",") if s.strip()) if order_raw else _DEFAULT_SUBTYPE_ORDER
    gate_enabled = os.getenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "false").strip().lower() not in {"false", "0", "off", "no"}
    floor = _env_float("BSI_CONFIDENCE_V1_FLOOR", BSI_CONFIDENCE_V1_FLOOR_DEFAULT)
    last_signal: StrategySignal | None = None
    for subtype in order:
        evaluator = _SUBTYPE_EVALUATORS.get(subtype)
        if evaluator is None:
            continue
        subtype_status = bsi_subtype_activation_status(subtype)
        if subtype_status == DISABLED:
            last_signal = _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=f"SUBTYPE_DISABLED_{subtype}")
            continue
        signal = evaluator(ctx)
        if signal.evidence is not None:
            signal.evidence["subtype_activation_status"] = subtype_status
        if signal.valid:
            try:
                score_result = score_live_signal(
                    subtype=subtype, canonical_symbol=signal.symbol, direction=signal.direction, generated_at=signal.generated_at,
                    entry=float(signal.proposed_entry), stop_loss=float(signal.stop_loss),
                    thesis=(signal.metadata or {}).get("bsi_thesis"), evidence=signal.evidence,
                )
                if signal.evidence is not None:
                    signal.evidence["bsi_confidence_v1_score"] = score_result["composite_score"]
                    signal.evidence["bsi_confidence_v1_version"] = score_result["confidence_version"]
            except Exception:
                score_result = None  # scoring must never itself break signal generation -- fail open, evidence simply omitted
            if gate_enabled and subtype == BSI_CONFIDENCE_V1_GATED_SUBTYPE and score_result is not None:
                passes, reason = bsi_under_over_floor_verdict(score_result["composite_score"], floor=floor)
                if not passes:
                    signal = _dc_replace(signal, valid=False, rejection_reason=reason)
        last_signal = signal
        if signal.valid:
            return signal
    if last_signal is not None:
        return last_signal
    return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_SUBTYPE_CONFIGURED")


# ============================================================================================
# Part 11 -- component attribution (RESEARCH-ONLY, not registered in EVALUATORS/STRATEGY_FAMILIES,
# never reachable from evaluate_all()/live replay's normal path -- backtest scripts import this
# directly). Reimplements the base order_flow ladder with each stage adding exactly ONE more
# mentor-rule gate on top of the previous stage, so a chronological backtest can measure whether
# each successive concept genuinely adds predictive value or merely reduces frequency with no
# edge improvement (the user's own explicit Part 11 question). Every stage still uses the SAME
# underlying primitives (_leg_bounds, _select_entry_array, _mentor_stop, _opposing_structural_
# level) as the real order_flow evaluator -- this does not reimplement detection, only which
# gates are enforced before a stage is allowed to produce a signal.
# ============================================================================================
COMPONENT_STAGES = (
    "structure_only",           # any qualifying M15 break in the HTF direction -- no liquidity/PD/FVG/OB/session gate at all
    "structure_liquidity",      # + require an opposing liquidity target to exist (_opposing_structural_level not None)
    "structure_liquidity_mss",  # + require the break to be a reversal (CHoCH/MSS), not a bare continuation BOS
    "plus_premium_discount",    # + require price to be in the leg's own discount/premium zone
    "plus_fvg",                 # + require an unmitigated FVG (not yet an order-block choice) in that zone
    "plus_ob",                  # + apply the full mentor OB-vs-FVG entry-array selection (small FVG -> FVG, large -> OB)
    "plus_session",             # + require the current bar to fall inside a recognized session box (Asian/London/NY)
    "full",                     # identical to evaluate_bsi_order_flow's own direct-confirmation path
)


def _stage_in_session_box(ctx: StrategyContext) -> bool:
    now = _last_bar_time(ctx)
    return _in_lunch_window(now) or _in_ny_session_window(now) or (7 <= now.hour < 10)  # + London (07:00-10:00 UTC, matches configuration.py's own default window)


def evaluate_bsi_component_stage(ctx: StrategyContext, stage: str) -> StrategySignal:
    """RESEARCH-ONLY component-attribution evaluator for Part 11 -- see module section header.
    Not part of the SHADOW-only production surface (not in EVALUATORS/STRATEGY_FAMILIES);
    imported directly by backtest scripts only."""
    tf = f"M15(component:{stage})"
    strategy_id = f"{_STRATEGY_ID}__component_{stage}"

    def _reject(reason: str) -> StrategySignal:
        return _no_signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, reason=reason)

    if stage not in COMPONENT_STAGES:
        return _reject(f"UNKNOWN_STAGE_{stage}")
    if stage == "full":
        signal = evaluate_bsi_order_flow(ctx)
        return StrategySignal(**{**signal.__dict__, "strategy_id": strategy_id})

    if not _spread_within_safety_buffer(ctx):
        return _reject("SPREAD_SAFETY_BUFFER_EXCEEDED")
    htf_direction = ctx.htf_trend_h4
    if htf_direction not in (_BULLISH, _BEARISH):
        return _reject("NO_HTF_BIAS")
    direction = _trade_direction(htf_direction)

    kinds = _MENTOR_ALL_BREAK_KINDS
    if stage in {"structure_liquidity_mss", "plus_premium_discount", "plus_fvg", "plus_ob", "plus_session"}:
        kinds = _MENTOR_MSS_KINDS  # stage 3+: require reversal (CHoCH/MSS), not bare continuation
    brk = _latest_break(ctx, direction=htf_direction, kinds=kinds)
    if brk is None:
        return _reject("NO_QUALIFYING_STRUCTURE_BREAK")

    price = _current_price(ctx)
    atr_dec = ctx.atr_m15 or Decimal("0.0001")
    bars = _bars(ctx)

    if stage == "structure_only":
        entry = Decimal(str(price))
        stop, stop_reason = _dynamic_stop(ctx, direction, entry, brk.broken_level, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
        if stop is None:
            return _reject(stop_reason)
        target = entry + abs(entry - stop) * Decimal("2.0") if direction == "LONG" else entry - abs(entry - stop) * Decimal("2.0")
        return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=50.0,
                        entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id}, metadata={})

    target = _opposing_structural_level(ctx, direction)
    if target is None:
        return _reject("NO_OPPOSING_LIQUIDITY_TARGET")
    if stage in {"structure_liquidity", "structure_liquidity_mss"}:
        entry = Decimal(str(price))
        stop, stop_reason = _dynamic_stop(ctx, direction, entry, brk.broken_level, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
        if stop is None:
            return _reject(stop_reason)
        return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=55.0,
                        entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id}, metadata={})

    leg = _leg_bounds(ctx, brk)
    if leg is None:
        return _reject("NO_LEG_FOR_FIBONACCI_ANCHOR")
    leg_low, leg_high, _leg_start = leg
    if not _zone_favorable(direction, leg_low, leg_high, price):
        return _reject("NOT_IN_PREMIUM_DISCOUNT_ZONE")
    if stage == "plus_premium_discount":
        entry = Decimal(str(price))
        stop, stop_reason = _dynamic_stop(ctx, direction, entry, leg_low if direction == "LONG" else leg_high, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
        if stop is None:
            return _reject(stop_reason)
        return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=60.0,
                        entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id}, metadata={})

    fvgs = _unmitigated_fvgs_in_zone(ctx, direction, leg_low, leg_high)
    if not fvgs:
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    if not any(_price_in_array(direction, _EntryArray(kind="fvg", low=z.price_low, high=z.price_high, fvg_id=z.id, size_atr=z.strength, ref_bar_index=0), price) for z in fvgs):
        return _reject("PRICE_NOT_IN_ENTRY_ZONE")
    if stage == "plus_fvg":
        chosen = fvgs[0]
        entry = Decimal(str(price))
        stop, stop_reason = _dynamic_stop(ctx, direction, entry, chosen.price_low if direction == "LONG" else chosen.price_high, atr_dec, min_atr_mult=0.3, max_atr_mult=5.0)
        if stop is None:
            return _reject(stop_reason)
        return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=65.0,
                        entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id}, metadata={})

    array = _select_entry_array(ctx, direction, leg_low, leg_high, bars)
    if array is None or not _price_in_array(direction, array, price):
        return _reject("NO_UNMITIGATED_ENTRY_ARRAY")
    if stage == "plus_ob":
        entry = Decimal(str(price))
        stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode="tight", leg_low=leg_low, leg_high=leg_high)
        if stop is None:
            return _reject(stop_reason)
        return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=70.0,
                        entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id, "entry_array_kind": array.kind}, metadata={})

    # plus_session
    if not _stage_in_session_box(ctx):
        return _reject("OUTSIDE_RECOGNIZED_SESSION_BOX")
    entry = Decimal(str(price))
    stop, stop_reason = _mentor_stop(ctx, direction, entry, array, atr_dec, stop_mode="tight", leg_low=leg_low, leg_high=leg_high)
    if stop is None:
        return _reject(stop_reason)
    return _signal(ctx, strategy_id=strategy_id, family=_STRATEGY_ID, timeframe=tf, direction=direction, strength=72.0,
                    entry=entry, stop=stop, target=target, evidence={"stage": stage, "structure_break_id": brk.id, "entry_array_kind": array.kind}, metadata={})

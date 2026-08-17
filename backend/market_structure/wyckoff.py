"""Wyckoff accumulation/distribution schematic detection.

Point-in-time-safe, and deliberately reuses existing market-structure primitives (confirmed
swings, structure breaks/BOS-CHoCH-MSS, liquidity sweeps, ATR) rather than duplicating any of
them -- this module contains NO independent pivot detector, NO independent break/CHoCH logic, and
NO independent sweep detector. It only composes backend.market_structure.engine's already-computed
MarketStructureSnapshot into the specific event sequence Wyckoff analysis cares about. Not wired
into engine.py::analyze_bars() (so every other strategy/replay/schema consumer is unaffected by
this module's existence) -- callers (mt5_strategies/families.py::evaluate_wyckoff) call
analyze_wyckoff() directly, the same way families.py already derives extra per-strategy readings
(e.g. _eqh_eql_touch_count) from an already-computed snapshot without mutating it.

VOLUME CAVEAT -- read before touching climax/effort-vs-result logic: Bensim's only per-bar
volume-like signal for MT5-sourced forex data is `tick_volume`, a COUNT OF PRICE TICKS in the bar
-- not traded contract/lot size and not real bid/ask executed volume. `real_volume` exists on the
MT5 candle schema but is 0/unpopulated for retail FX on this deployment (see
backend/brokers/mt5/candles.py::candle_from_raw). This is the exact same limitation already
documented in market_structure/volume_delta.py's own module docstring for cumulative volume delta.
Every "volume"/"effort" reference below is really "tick-count activity" -- a reasonable, standard
proxy for participation that every public CVD/volume-profile implementation for retail-broker
OHLCV data uses, but it is NOT literally traded size, and classical Wyckoff volume analysis
(which assumes real executed volume) is not literally available here. This module never claims
otherwise; every function that reads tick_volume says so in its own docstring too.

SCOPE CAVEAT: live and replay strategy evaluation only ever supply ~100 M15 bars (~25 hours) --
see backend/historical_intelligence/replay.py::STRATEGY_LOOKBACK, which documents why widening a
strategy's fetch window beyond what every other family already receives is a deliberate,
separately-justified change, not something to do casually. Classical Wyckoff campaigns illustrated
in the literature run on daily charts over weeks; what this module can actually detect within a
~25-hour window is a SHORT-DURATION (intraday/multi-hour) accumulation or distribution schematic --
structurally the same principles, applied at a smaller scale, not the multi-week institutional
campaigns Wyckoff literature usually shows. Documented here as a real, deliberate scope boundary,
not hidden or silently claimed away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.market_structure.bar_utils import StructureBar, average_true_range, normalize_bars
from backend.market_structure.models import MarketStructureSnapshot, StructureBreakKind

# --- tunables (coarse, economically-motivated -- not fit to any historical sample) -----------
_PCTL_LOOKBACK = 30            # trailing bars for tick-volume / true-range percentile ranks
_CLIMAX_PERCENTILE = 0.85      # both true-range and tick-volume must clear this trailing percentile to call a bar climactic
_TREND_LOOKBACK = 15           # bars scanned before a climax candidate for a genuine prior directional move
_TREND_MIN_ATR_MOVE = 2.0      # minimum net ATR-multiple move required to call it a real prior decline/advance
_CLIMAX_CLOSE_POSITION = 0.5   # SC must close in the upper half of its own bar's range, BC in the lower half
_AR_MAX_LOOKAHEAD_BARS = 25    # how far after a climax bar to look for the Automatic Rally/Reaction swing
_RANGE_MAX_AGE_BARS = 90       # do not search for a climax older than this within the available window
_ST_TOLERANCE_ATR = 0.5        # a secondary test must stay within this many ATR of the climax extreme to "hold"
_SOS_MIN_BREAK_ATR = 0.3       # minimum break_distance_atr accepted from snapshot.breaks to count as SOS/SOW
_PHASE_E_MIN_ATR_BEYOND = 0.5  # confirmed close this many ATR beyond the range boundary => Phase E (markup/markdown)
_MIN_BARS = 30


@dataclass(frozen=True)
class WyckoffEvent:
    event_type: str  # PS | PSY | SC | BC | AR | ST | SPRING | UTAD | SOS | SOW | LPS | LPSY
    bar_index: int
    time: datetime
    price: float
    direction: str  # "bullish" = evidence toward accumulation, "bearish" = evidence toward distribution
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WyckoffAnalysis:
    schematic: str  # "accumulation" | "distribution" | "none"
    phase: str      # "no_range_detected" | "A" | "B" | "C_untested" | "C" | "D" | "E" | "invalidated"
    range_high: float | None
    range_low: float | None
    # (last_close - range_low) / (range_high - range_low). Deliberately NOT clipped to [0, 1] --
    # a Spring/UTAD penetration is meaningfully <0 or >1, and clipping would erase exactly the
    # information a spring/UTAD test carries.
    range_position: float | None
    events: list[WyckoffEvent] = field(default_factory=list)
    invalidated: bool = False
    invalidation_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def event(self, event_type: str) -> WyckoffEvent | None:
        matches = [e for e in self.events if e.event_type == event_type]
        return matches[-1] if matches else None

    def has(self, event_type: str) -> bool:
        return self.event(event_type) is not None


def _percentile_rank(values: list[float], index: int, lookback: int = _PCTL_LOOKBACK) -> float | None:
    """Trailing-window percentile rank of values[index] within values[index-lookback+1 : index+1]
    -- only ever looks backward from `index`, so this is point-in-time-safe by construction: a
    replay at bar `index` sees exactly the same value a live cycle would have seen at that bar."""
    start = max(0, index - lookback + 1)
    window = values[start: index + 1]
    if len(window) < 5:
        return None
    v = values[index]
    return sum(1 for x in window if x <= v) / len(window)


def effort_vs_result(bars: list[StructureBar], tick_volumes: list[float]) -> list[str]:
    """Per-bar effort(tick-volume activity)-vs-result(true range) classification -- Wyckoff's own
    diagnostic principle, exposed as observability evidence attached to events below, never itself
    a standalone trigger. 'high_effort_low_result' (heavy tick activity, little net range) is the
    classic absorption signature that typically precedes a genuine SC/BC/Spring/UTAD reversal;
    'high_effort_high_result' is the confirmation signature expected at a genuine SOS/SOW."""
    ranges = [float(b.high - b.low) for b in bars]
    out: list[str] = []
    for i in range(len(bars)):
        vp = _percentile_rank(tick_volumes, i)
        rp = _percentile_rank(ranges, i)
        if vp is None or rp is None:
            out.append("insufficient_data")
        elif vp >= 0.7 and rp < 0.4:
            out.append("high_effort_low_result")
        elif vp >= 0.7 and rp >= 0.6:
            out.append("high_effort_high_result")
        elif vp < 0.3 and rp < 0.3:
            out.append("low_effort_low_result")
        else:
            out.append("neutral")
    return out


def _net_atr_move(closes: list[float], atrs: list[Any], end_index: int, lookback: int) -> float | None:
    start = max(0, end_index - lookback)
    if start >= end_index or end_index < 0 or end_index >= len(atrs):
        return None
    atr = atrs[end_index]
    if not atr or float(atr) <= 0:
        return None
    return (closes[end_index] - closes[start]) / float(atr)


def _find_climax(bars: list[StructureBar], atrs: list[Any], tick_volumes: list[float], closes: list[float]) -> tuple[int, str] | None:
    """Most recent Selling Climax ("SC", accumulation-supporting) or Buying Climax ("BC",
    distribution-supporting) candidate within _RANGE_MAX_AGE_BARS, searching BACKWARD from the
    newest bar so a live/replay cycle always reacts to the freshest plausible range rather than a
    stale one. Every check at bar i reads only bars <= i (point-in-time-safe)."""
    ranges = [float(b.high - b.low) for b in bars]
    n = len(bars)
    earliest = max(_TREND_LOOKBACK, n - _RANGE_MAX_AGE_BARS)
    for i in range(n - 1, earliest - 1, -1):
        vp = _percentile_rank(tick_volumes, i)
        rp = _percentile_rank(ranges, i)
        if vp is None or rp is None or vp < _CLIMAX_PERCENTILE or rp < _CLIMAX_PERCENTILE:
            continue
        bar = bars[i]
        candle_range = float(bar.high - bar.low)
        if candle_range <= 0:
            continue
        close_position = float(bar.close - bar.low) / candle_range
        prior_move = _net_atr_move(closes, atrs, i - 1, _TREND_LOOKBACK)
        if prior_move is None:
            continue
        window = bars[max(0, i - _TREND_LOOKBACK): i]
        if not window:
            continue
        window_lows = [float(b.low) for b in window]
        window_highs = [float(b.high) for b in window]
        # Selling Climax: prior decline, this bar undercuts the recent lows, closes off the low.
        if prior_move <= -_TREND_MIN_ATR_MOVE and float(bar.low) <= min(window_lows) and close_position >= _CLIMAX_CLOSE_POSITION:
            return i, "SC"
        # Buying Climax: prior advance, this bar exceeds the recent highs, closes off the high.
        if prior_move >= _TREND_MIN_ATR_MOVE and float(bar.high) >= max(window_highs) and close_position <= (1.0 - _CLIMAX_CLOSE_POSITION):
            return i, "BC"
    return None


def _swings_after(swings, bar_index: int, swing_type: str, *, max_bar_index: int | None = None):
    out = [s for s in swings if s.swing_type == swing_type and s.bar_index > bar_index and (max_bar_index is None or s.bar_index <= max_bar_index)]
    return sorted(out, key=lambda s: s.bar_index)


def _swings_before(swings, bar_index: int, swing_type: str, *, min_bar_index: int | None = None):
    out = [s for s in swings if s.swing_type == swing_type and s.bar_index < bar_index and (min_bar_index is None or s.bar_index >= min_bar_index)]
    return sorted(out, key=lambda s: s.bar_index)


def analyze_wyckoff(rows: list[dict[str, Any]], snapshot: MarketStructureSnapshot, *, symbol: str, timeframe: str = "M15") -> WyckoffAnalysis:
    """Single entry point. `rows` must be the SAME raw candle-row list the snapshot's timeframe
    was built from (chronologically ordered, as every live/replay caller already provides -- see
    module docstring re: positional tick_volume alignment). `snapshot` must already be
    analyze_bars()'s output for that same `rows`/timeframe -- this function performs no structure
    detection of its own, only composition."""
    bars = normalize_bars(rows, symbol=symbol, timeframe=timeframe)
    n = len(bars)
    empty = WyckoffAnalysis(schematic="none", phase="no_range_detected", range_high=None, range_low=None, range_position=None)
    if n < _MIN_BARS:
        return WyckoffAnalysis(**{**empty.__dict__, "warnings": ["insufficient_bars"]})

    # tick_volume is read directly off the raw rows (matching families.py::evaluate_vwap_reversion's
    # own precedent) rather than via StructureBar.volume, which MT5 rows never populate (see module
    # docstring). Relies on `rows` and `bars` sharing positional order -- true for every real caller
    # in this codebase (rows are always already chronologically sorted); guarded explicitly below
    # rather than trusted silently.
    if len(rows) != n:
        return WyckoffAnalysis(**{**empty.__dict__, "warnings": ["bar_row_count_mismatch"]})
    tick_volumes = [float(row.get("tick_volume") or 0.0) for row in rows]

    atrs = average_true_range(bars, 14)
    closes = [float(b.close) for b in bars]
    classifications = effort_vs_result(bars, tick_volumes)

    def _mk(event_type: str, bar_index: int, time: datetime, price: float, direction: str, evidence: dict[str, Any] | None = None) -> WyckoffEvent:
        ev = dict(evidence or {})
        if 0 <= bar_index < len(classifications):
            ev.setdefault("effort_result", classifications[bar_index])
        return WyckoffEvent(event_type=event_type, bar_index=bar_index, time=time, price=price, direction=direction, evidence=ev)

    climax = _find_climax(bars, atrs, tick_volumes, closes)
    if climax is None:
        return WyckoffAnalysis(**{**empty.__dict__, "warnings": ["no_climax_detected"]})
    climax_index, climax_kind = climax
    schematic = "accumulation" if climax_kind == "SC" else "distribution"
    schematic_direction = "bullish" if schematic == "accumulation" else "bearish"
    climax_bar = bars[climax_index]
    events: list[WyckoffEvent] = [
        _mk(climax_kind, climax_index, climax_bar.close_time, float(climax_bar.low if climax_kind == "SC" else climax_bar.high), schematic_direction,
            {"tick_volume_percentile": _percentile_rank(tick_volumes, climax_index), "true_range_percentile": _percentile_rank([float(b.high - b.low) for b in bars], climax_index)})
    ]

    swings = snapshot.swings
    ps_type = "low" if schematic == "accumulation" else "high"

    # Preliminary Support / Preliminary Supply -- optional, evidence-only. Only recorded if a
    # genuinely elevated-volume same-type swing exists shortly before the climax; never fabricated
    # when absent (real Wyckoff campaigns frequently lack a clean PS/PSY -- that is normal).
    earlier_ps = _swings_before(swings, climax_index, ps_type, min_bar_index=max(0, climax_index - _TREND_LOOKBACK))
    if earlier_ps:
        candidate = earlier_ps[-1]
        vp = _percentile_rank(tick_volumes, candidate.bar_index)
        if vp is not None and vp >= 0.6:
            events.append(_mk("PS" if schematic == "accumulation" else "PSY", candidate.bar_index, candidate.confirmation_time or candidate.detected_time,
                               float(candidate.price), schematic_direction, {"tick_volume_percentile": vp}))

    # Automatic Rally / Automatic Reaction -- the opposite-type swing following the climax. This
    # is what actually defines the range's SECOND boundary (the climax bar itself defines the
    # first). No AR yet => Phase A is still incomplete; do not fabricate a range.
    ar_type = "high" if schematic == "accumulation" else "low"
    ar_candidates = _swings_after(swings, climax_index, ar_type, max_bar_index=climax_index + _AR_MAX_LOOKAHEAD_BARS)
    ar = ar_candidates[0] if ar_candidates else None
    if ar is None:
        return WyckoffAnalysis(schematic=schematic, phase="A", range_high=None, range_low=None, range_position=None,
                                events=events, warnings=["automatic_rally_reaction_not_yet_confirmed"])

    range_low = float(climax_bar.low) if schematic == "accumulation" else float(ar.price)
    range_high = float(ar.price) if schematic == "accumulation" else float(climax_bar.high)
    range_span = range_high - range_low
    events.append(_mk("AR", ar.bar_index, ar.confirmation_time or ar.detected_time, float(ar.price), schematic_direction))
    if range_span <= 0:
        return WyckoffAnalysis(schematic=schematic, phase="A", range_high=None, range_low=None, range_position=None,
                                events=events, warnings=["degenerate_range"])

    boundary_atr = float(atrs[n - 1]) if atrs[n - 1] else None
    boundary_price = range_low if schematic == "accumulation" else range_high
    st_tolerance = boundary_atr * _ST_TOLERANCE_ATR if boundary_atr else range_span * 0.1

    # Secondary Test(s) -- same-type swings after AR that revisit the climax extreme without
    # decisively breaking it (Phase B, "building the cause"). Any same-type swing that DOES break
    # the extreme is simply not recorded here (it is either a later Spring/UTAD, handled
    # separately via liquidity sweeps below, or genuine invalidation) -- no fabricated confidence.
    st_candidates = _swings_after(swings, ar.bar_index, ps_type, max_bar_index=n - 1)
    sts = []
    for s in st_candidates:
        holds = float(s.price) >= range_low - st_tolerance if schematic == "accumulation" else float(s.price) <= range_high + st_tolerance
        if holds:
            sts.append(s)
            events.append(_mk("ST", s.bar_index, s.confirmation_time or s.detected_time, float(s.price), schematic_direction,
                               {"tick_volume_percentile": _percentile_rank(tick_volumes, s.bar_index)}))

    # Spring / Upthrust(-after-distribution) -- reuses the EXISTING liquidity-sweep detector
    # directly: a Spring is structurally identical to a sell-side sweep of the range low that
    # reclaims (liquidity.py's own "breached and reclaimed" definition), and a UTAD is the
    # buy-side mirror at the range high. liquidity.py already assigns direction=BULLISH to a
    # sell-side reclaim and direction=BEARISH to a buy-side reclaim -- exactly the reversal
    # implication Wyckoff assigns to Spring/UTAD respectively. No second sweep detector here.
    spring_side = "sell_side" if schematic == "accumulation" else "buy_side"
    sweep_tolerance = boundary_atr * 1.5 if boundary_atr else range_span * 0.15
    candidate_sweeps = [
        s for s in snapshot.liquidity_sweeps
        if s.side == spring_side and s.bar_index > ar.bar_index and abs(float(s.swept_price) - boundary_price) <= sweep_tolerance
    ]
    spring = min(candidate_sweeps, key=lambda s: s.bar_index) if candidate_sweeps else None
    spring_tested_successfully = False
    spring_invalidated = False
    if spring is not None:
        events.append(_mk("SPRING" if schematic == "accumulation" else "UTAD", spring.bar_index, spring.confirmation_time or spring.detected_time,
                           float(spring.swept_price), schematic_direction,
                           {"penetration": float(spring.penetration), "reclaim_price": float(spring.reclaim_price) if spring.reclaim_price is not None else None,
                            "tick_volume_percentile": _percentile_rank(tick_volumes, spring.bar_index)}))
        post_swings = _swings_after(swings, spring.bar_index, ps_type, max_bar_index=n - 1)
        if post_swings:
            candidate = post_swings[0]
            broke_past_extreme = float(candidate.price) < float(spring.swept_price) if schematic == "accumulation" else float(candidate.price) > float(spring.swept_price)
            if broke_past_extreme:
                spring_invalidated = True
            else:
                spring_tested_successfully = True
                events.append(_mk("ST", candidate.bar_index, candidate.confirmation_time or candidate.detected_time, float(candidate.price), schematic_direction,
                                   {"post_spring_test": True}))

    # Sign of Strength / Sign of Weakness (Phase D) -- reuses the EXISTING structure-break
    # detector directly (BOS/CHoCH/MSS already computed for every strategy). No independent break
    # logic here. Only searched for after AR (or after a confirmed Spring/UTAD test when present).
    sos_direction = schematic_direction
    after_bar = spring.bar_index if spring is not None else ar.bar_index
    sos_candidates = [
        b for b in snapshot.breaks
        if b.direction == sos_direction and b.bar_index > after_bar
        and b.break_kind in {StructureBreakKind.BOS.value, StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value}
        and (b.break_distance_atr or 0.0) >= _SOS_MIN_BREAK_ATR
    ]
    sos = min(sos_candidates, key=lambda b: b.bar_index) if sos_candidates else None
    lps = None
    if sos is not None:
        events.append(_mk("SOS" if schematic == "accumulation" else "SOW", sos.bar_index, sos.confirmation_time or sos.detected_time,
                           float(sos.break_price), sos_direction,
                           {"break_kind": sos.break_kind, "break_distance_atr": sos.break_distance_atr, "broken_level": float(sos.broken_level)}))
        lps_type = "low" if schematic == "accumulation" else "high"
        lps_tolerance = boundary_atr * 0.75 if boundary_atr else range_span * 0.1
        broken_level = float(sos.broken_level)
        for s in _swings_after(swings, sos.bar_index, lps_type, max_bar_index=n - 1):
            holds = float(s.price) >= broken_level - lps_tolerance if schematic == "accumulation" else float(s.price) <= broken_level + lps_tolerance
            if holds:
                lps = s
                events.append(_mk("LPS" if schematic == "accumulation" else "LPSY", s.bar_index, s.confirmation_time or s.detected_time,
                                   float(s.price), sos_direction))
                break

    # Phase E -- price has confirmed (closed, not just wicked) beyond the range boundary by a
    # meaningful ATR multiple: markup/markdown genuinely underway, not just testing the edge.
    last_close = closes[-1]
    last_atr = float(atrs[-1]) if atrs[-1] else None
    phase_e = bool(last_atr) and (
        (schematic == "accumulation" and last_close > range_high + _PHASE_E_MIN_ATR_BEYOND * last_atr)
        or (schematic == "distribution" and last_close < range_low - _PHASE_E_MIN_ATR_BEYOND * last_atr)
    )

    if spring_invalidated:
        phase = "invalidated"
    elif phase_e:
        phase = "E"
    elif sos is not None:
        phase = "D"
    elif spring is not None and spring_tested_successfully:
        phase = "C"
    elif spring is not None:
        phase = "C_untested"
    elif sts:
        phase = "B"
    else:
        phase = "A"

    range_position = (last_close - range_low) / range_span

    return WyckoffAnalysis(
        schematic=schematic,
        phase=phase,
        range_high=range_high,
        range_low=range_low,
        range_position=range_position,
        events=events,
        invalidated=spring_invalidated,
        invalidation_reason=("spring_low_broken" if schematic == "accumulation" else "utad_high_broken") if spring_invalidated else None,
    )

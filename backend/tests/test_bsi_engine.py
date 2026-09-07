"""bsi -- Faiz SMC course, mentor-faithful strategy family.

Covers: the pure mentor-rule helper functions against hand-built objects/bars (deterministic,
does not depend on organic fractal-swing detection outcomes, which are fragile to engineer by
hand for every subtype's exact geometry), each subtype's evaluator end-to-end through
StrategyContext (valid-or-meaningfully-rejected, same convention test_wyckoff.py already
established for exactly this kind of hard-to-hand-engineer synthetic-swing scenario), and
registration safety (disabled by default, reachable by replay).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.models import (
    Direction,
    ImbalanceZone,
    LiquidityLevel,
    LiquiditySide,
    StructureBreak,
    StructureBreakKind,
    SwingPoint,
)
from backend.mt5_strategies.context import build_strategy_context
from backend.mt5_strategies.families import EVALUATORS, evaluate_bsi
from backend.mt5_strategies.families.bsi_engine import (
    _abc_internal_break,
    _close_based_fakeout_reclaim,
    _fakeout_quality_score,
    _find_abc_legs,
    _in_930_window,
    _in_lunch_window,
    _in_ny_session_window,
    _leg_bounds,
    _mentor_equal_levels,
    _mentor_order_block_for_fvg,
    _p1_already_broken,
    _tp_bounded,
    _tp_fixed_rr,
    _zone_favorable,
    evaluate_bsi_abc,
    evaluate_bsi_abcd,
    evaluate_bsi_asian,
    evaluate_bsi_new_york,
    evaluate_bsi_0930,
    evaluate_bsi_order_flow,
    evaluate_bsi_under_over,
)
from backend.mt5_strategies.models import DISABLED, STRATEGY_FAMILIES, activation_status

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def _row(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": (NOW + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": l, "close": c, "tick_volume": 500, "spread": 2}


def _leg_rows(steps: int, step_size: float, *, start_price: float = 1.1000, start_index: int = 0) -> tuple[list[dict], float, int]:
    rows: list[dict] = []
    price = start_price
    i = start_index
    for _ in range(steps):
        o = price
        price += step_size
        c = price
        hi = max(o, c) + abs(step_size) * 0.15
        lo = min(o, c) - abs(step_size) * 0.15
        rows.append(_row(i, o, hi, lo, c))
        i += 1
    return rows, price, i


def _flat_rows(n: int, price: float = 1.1000) -> list[dict]:
    return [_row(i, price, price + 0.0003, price - 0.0003, price) for i in range(n)]


def _swing(swing_type: str, bar_index: int, price: float, *, id_suffix: str = "") -> SwingPoint:
    t = NOW + timedelta(minutes=15 * bar_index)
    return SwingPoint(
        id=f"swg_{swing_type}_{bar_index}{id_suffix}", symbol="EURUSD", timeframe="M15",
        start_time=t, end_time=t, detected_time=t, confirmation_time=t,
        price_low=Decimal(str(price)), price_high=Decimal(str(price)),
        direction=Direction.BULLISH if swing_type == "high" else Direction.BEARISH,
        configuration_version="1.0", configuration_hash="deadbeef",
        swing_type=swing_type, bar_index=bar_index, price=Decimal(str(price)), candidate_time=t,
    )


def _break(*, direction: str, bar_index: int, broken_level: float, break_price: float, kind: str = StructureBreakKind.BOS.value, broken_swing_id: str = "swg_x") -> StructureBreak:
    t = NOW + timedelta(minutes=15 * bar_index)
    return StructureBreak(
        id=f"brk_{bar_index}_{kind}", symbol="EURUSD", timeframe="M15",
        start_time=t, end_time=t, detected_time=t, confirmation_time=t,
        price_low=Decimal("0"), price_high=Decimal("1"), direction=direction,
        configuration_version="1.0", configuration_hash="deadbeef",
        break_kind=kind, broken_swing_id=broken_swing_id, broken_level=Decimal(str(broken_level)),
        break_price=Decimal(str(break_price)), break_distance=Decimal("0.001"),
        confirmation_mode="close", bar_index=bar_index, explanation="test",
    )


def _fvg(*, direction: str, low: float, high: float, status: str = "active", bar_indexes: list[int] | None = None, strength: float | None = None) -> ImbalanceZone:
    t = NOW
    return ImbalanceZone(
        id=f"fvg_{low}_{high}", symbol="EURUSD", timeframe="M15",
        start_time=t, end_time=t, detected_time=t, confirmation_time=t,
        price_low=Decimal(str(low)), price_high=Decimal(str(high)), direction=direction, status=status,
        strength=strength, configuration_version="1.0", configuration_hash="deadbeef",
        supporting_bar_indexes=bar_indexes or [10, 11, 12],
        midpoint=Decimal(str((low + high) / 2)), consequent_encroachment=Decimal(str((low + high) / 2)),
    )


class _FakeSnapshot:
    def __init__(self, *, swings=(), breaks=()):
        self.swings = list(swings)
        self.breaks = list(breaks)


class _FakeCtx:
    def __init__(self, *, swings=(), breaks=()):
        self.m15_snapshot = _FakeSnapshot(swings=swings, breaks=breaks)


# --------------------------------------------------------------------- _mentor_htf_direction ---
# BSI Daily Bias Audit (2026-09-02, BSI_DAILY_BIAS_AUDIT.md): the mentor's own "high time frame"
# reference is Daily, not H4 (zero H4/4-hour mentions across all 40 course transcripts; "daily
# bias" used explicitly and repeatedly). _mentor_htf_direction() prefers ctx.htf_trend_daily,
# falling back to ctx.htf_trend_h4 only when Daily hasn't been computed for this call site yet --
# a deliberate safety net against silently demoting the 4 affected subtypes (order_flow/abc/0930/
# abcd) to permanent NO_HTF_BIAS in any not-yet-updated caller, never a hedge against the finding
# itself.
class _HtfFakeCtx:
    def __init__(self, *, htf_trend_h4: str, htf_trend_daily: str | None):
        self.htf_trend_h4 = htf_trend_h4
        self.htf_trend_daily = htf_trend_daily


def test_mentor_htf_direction_prefers_daily_when_available():
    import backend.mt5_strategies.families.bsi_engine as eng
    ctx = _HtfFakeCtx(htf_trend_h4="bearish", htf_trend_daily="bullish")
    assert eng._mentor_htf_direction(ctx) == "bullish"


def test_mentor_htf_direction_falls_back_to_h4_when_daily_is_none():
    import backend.mt5_strategies.families.bsi_engine as eng
    ctx = _HtfFakeCtx(htf_trend_h4="bearish", htf_trend_daily=None)
    assert eng._mentor_htf_direction(ctx) == "bearish"


def test_mentor_htf_direction_daily_unknown_is_still_preferred_over_h4_directional():
    """Daily being genuinely computed but non-directional (ranging/transitional/unknown) is NOT
    the same as "not yet computed" (None) -- it must still take precedence over H4, not silently
    fall back, since a real Daily read (even a non-directional one) is more mentor-faithful
    information than an H4 approximation the audit found is frequently wrong."""
    import backend.mt5_strategies.families.bsi_engine as eng
    ctx = _HtfFakeCtx(htf_trend_h4="bullish", htf_trend_daily="ranging")
    assert eng._mentor_htf_direction(ctx) == "ranging"


# --------------------------------------------------------------------- _leg_bounds / _zone_favorable ---
def test_leg_bounds_anchors_on_the_structure_breaking_leg_not_freshest_high_low():
    """Row 4: the Fibonacci anchor must be the SPECIFIC impulsive leg that caused THIS break --
    an older, unrelated swing further back must never be picked just because it also exists."""
    swings = [_swing("low", 5, 1.1000), _swing("high", 10, 1.1100), _swing("low", 20, 1.1050)]
    brk = _break(direction="bullish", bar_index=25, broken_level=1.1100, break_price=1.1180)
    ctx = _FakeCtx(swings=swings)
    result = _leg_bounds(ctx, brk)
    assert result is not None
    low, high, leg_start = result
    # leg_start must be the LOW swing immediately before the break (bar_index 20), not the older
    # low at bar_index 5.
    assert leg_start.bar_index == 20
    assert low == Decimal("1.105")
    assert high == Decimal("1.118")


def test_leg_bounds_returns_none_without_a_preceding_opposite_swing():
    ctx = _FakeCtx(swings=[_swing("high", 10, 1.1100)])
    brk = _break(direction="bullish", bar_index=25, broken_level=1.1100, break_price=1.1180)
    assert _leg_bounds(ctx, brk) is None


def test_zone_favorable_gate_is_exactly_the_0_5_midpoint():
    """Row 5: the mentor's premium/discount split is exactly 0.5, no OTE."""
    low, high = Decimal("1.1000"), Decimal("1.1100")
    midpoint = 1.1050
    assert _zone_favorable("LONG", low, high, midpoint - 0.0001) is True
    assert _zone_favorable("LONG", low, high, midpoint + 0.0001) is False
    assert _zone_favorable("SHORT", low, high, midpoint + 0.0001) is True
    assert _zone_favorable("SHORT", low, high, midpoint - 0.0001) is False


# --------------------------------------------------------------------------- mentor order block ---
def test_mentor_order_block_is_the_fvgs_own_first_candle_not_break_anchored_scan():
    """Row 9: OB = the first candle of the SPECIFIC 3-candle sequence forming THIS fvg -- the
    mentor explicitly rejects 'last opposite candle before the break' (zones.py's own rule)."""
    bars = [StructureBar(index=i, symbol="EURUSD", timeframe="M15", open_time=NOW, close_time=NOW,
                          open=Decimal("1.10"), high=Decimal("1.101"), low=Decimal("1.099"), close=Decimal("1.1005"))
            for i in range(5)]
    origin_bar = StructureBar(index=2, symbol="EURUSD", timeframe="M15", open_time=NOW, close_time=NOW,
                               open=Decimal("1.1050"), high=Decimal("1.1080"), low=Decimal("1.1020"), close=Decimal("1.1030"))
    bars[2] = origin_bar
    fvg = _fvg(direction="bullish", low=1.11, high=1.112, bar_indexes=[2, 3, 4])
    result = _mentor_order_block_for_fvg(fvg, bars)
    assert result == (origin_bar.low, origin_bar.high)


def test_mentor_order_block_returns_none_for_out_of_range_index():
    fvg = _fvg(direction="bullish", low=1.11, high=1.112, bar_indexes=[99])
    assert _mentor_order_block_for_fvg(fvg, []) is None


# --------------------------------------------------------------------------------- ABC legs ---
def test_abc_legs_invalidated_when_b_leg_crosses_a_leg_start():
    """Mentor's precise words: 'if the B leg crosses this [A leg] level, the trade setup becomes
    invalid'."""
    swings = [_swing("low", 0, 1.1000), _swing("high", 10, 1.1200), _swing("low", 20, 1.0950)]  # P2 (1.0950) < P0 (1.1000) -> invalid
    ctx = _FakeCtx(swings=swings)
    assert _find_abc_legs(ctx, "LONG") is None


def test_abc_legs_found_when_b_leg_stays_above_a_leg_start():
    swings = [_swing("low", 0, 1.1000), _swing("high", 10, 1.1200), _swing("low", 20, 1.1050)]  # P2 (1.1050) > P0 (1.1000) -> valid
    ctx = _FakeCtx(swings=swings)
    result = _find_abc_legs(ctx, "LONG")
    assert result is not None
    p0, p1, p2 = result
    assert (p0.bar_index, p1.bar_index, p2.bar_index) == (0, 10, 20)


def test_abc_internal_break_only_counts_when_located_inside_bc_zone():
    """Row 3: 'we are ONLY going to look for structure which is INSIDE the B leg and the C leg' --
    a break located elsewhere on the chart must not count."""
    p1 = _swing("high", 10, 1.1200)
    p2 = _swing("low", 20, 1.1050)
    outside_break = _break(direction="bullish", bar_index=25, broken_level=1.0900, break_price=1.0950)  # far below the [1.1050, 1.1200] zone
    inside_break = _break(direction="bullish", bar_index=26, broken_level=1.1100, break_price=1.1120)  # inside [1.1050, 1.1200]
    ctx = _FakeCtx(breaks=[outside_break, inside_break])
    result = _abc_internal_break(ctx, "LONG", p1, p2)
    assert result is not None
    assert result.bar_index == 26


def test_abc_internal_break_none_when_no_break_in_zone():
    p1 = _swing("high", 10, 1.1200)
    p2 = _swing("low", 20, 1.1050)
    outside_break = _break(direction="bullish", bar_index=25, broken_level=1.0900, break_price=1.0950)
    ctx = _FakeCtx(breaks=[outside_break])
    assert _abc_internal_break(ctx, "LONG", p1, p2) is None


def test_p1_already_broken_detects_sequencing_invalidation():
    p1 = _swing("high", 10, 1.1200)
    brk_matching = _break(direction="bullish", bar_index=30, broken_level=1.1200, break_price=1.1250, broken_swing_id=p1.id)
    ctx = _FakeCtx(breaks=[brk_matching])
    assert _p1_already_broken(ctx, p1) is True
    ctx_empty = _FakeCtx(breaks=[])
    assert _p1_already_broken(ctx_empty, p1) is False


# --------------------------------------------------------------------------- TP disciplines ---
def test_tp_fixed_rr_is_exactly_1_to_2_for_new_york_session():
    entry, stop = Decimal("1.1000"), Decimal("1.0950")
    target = _tp_fixed_rr(entry, stop, "LONG", 2.0)
    assert target == Decimal("1.1100")  # risk=0.0050, 2R above entry


def test_tp_bounded_rejects_below_minimum_rr():
    entry, stop = Decimal("1.1000"), Decimal("1.0990")  # risk = 0.0010
    natural_target = Decimal("1.1015")  # only 1.5R -- below the 3R minimum
    assert _tp_bounded(entry, stop, "LONG", natural_target, min_rr=3.0, max_rr=5.0) is None


def test_tp_bounded_clips_above_maximum_rr():
    entry, stop = Decimal("1.1000"), Decimal("1.0990")  # risk = 0.0010
    natural_target = Decimal("1.1100")  # 10R -- above the 5R ceiling
    target = _tp_bounded(entry, stop, "LONG", natural_target, min_rr=3.0, max_rr=5.0)
    assert target == Decimal("1.1050")  # clipped to exactly 5R


def test_tp_bounded_passes_through_within_band():
    entry, stop = Decimal("1.1000"), Decimal("1.0990")
    natural_target = Decimal("1.1035")  # 3.5R, within [3,5]
    assert _tp_bounded(entry, stop, "LONG", natural_target, min_rr=3.0, max_rr=5.0) == natural_target


# --------------------------------------------------------------------------- session/time gates ---
def test_lunch_window_is_the_asian_close_to_london_open_gap():
    assert _in_lunch_window(datetime(2026, 1, 5, 6, 30, tzinfo=timezone.utc)) is True
    assert _in_lunch_window(datetime(2026, 1, 5, 5, 59, tzinfo=timezone.utc)) is False
    assert _in_lunch_window(datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc)) is False


def test_ny_session_window_covers_both_configured_ny_boxes():
    assert _in_ny_session_window(datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)) is True
    assert _in_ny_session_window(datetime(2026, 1, 5, 19, 0, tzinfo=timezone.utc)) is True
    assert _in_ny_session_window(datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)) is False


def test_930_window_is_new_york_local_930_to_1159():
    # 14:30 UTC == 9:30 America/New_York during winter (EST, UTC-5)
    assert _in_930_window(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)) is True
    assert _in_930_window(datetime(2026, 1, 5, 14, 29, tzinfo=timezone.utc)) is False
    assert _in_930_window(datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)) is False  # 12:00 NY, past 11:59 window


# --------------------------------------------------------------------------- under_over ---
def test_mentor_equal_levels_filters_below_min_touches():
    low_touch = LiquidityLevel(id="l1", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW,
                                configuration_version="1.0", configuration_hash="x", side=LiquiditySide.SELL_SIDE,
                                level=Decimal("1.1000"), touch_count=2, source="equal_level")
    high_touch = LiquidityLevel(id="l2", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW,
                                 configuration_version="1.0", configuration_hash="x", side=LiquiditySide.SELL_SIDE,
                                 level=Decimal("1.2000"), touch_count=3, source="equal_level")
    ctx = _FakeCtx()
    ctx.m15_snapshot.equal_levels = [low_touch, high_touch]
    result = _mentor_equal_levels(ctx, min_touches=3)
    assert len(result) == 1
    assert result[0].id == "l2"


def test_close_based_fakeout_reclaim_ignores_wicks():
    """Under/Over's explicit rule: 'wicks do not matter, you just want to wait for the candle to
    close'."""
    level = Decimal("1.1000")
    bars = [
        StructureBar(index=0, symbol="E", timeframe="M15", open_time=NOW, close_time=NOW, open=Decimal("1.1010"), high=Decimal("1.1015"), low=Decimal("1.0990"), close=Decimal("1.1005")),  # wicks below level but CLOSES above -- must not count as a fakeout
        StructureBar(index=1, symbol="E", timeframe="M15", open_time=NOW, close_time=NOW, open=Decimal("1.1005"), high=Decimal("1.1006"), low=Decimal("1.0980"), close=Decimal("1.0985")),  # closes below -- genuine close-based fakeout
        StructureBar(index=2, symbol="E", timeframe="M15", open_time=NOW, close_time=NOW, open=Decimal("1.0985"), high=Decimal("1.1010"), low=Decimal("1.0980"), close=Decimal("1.1005")),  # closes back above -- reclaim
    ]
    result = _close_based_fakeout_reclaim(bars, level, LiquiditySide.SELL_SIDE.value, lookback_bars=10)
    assert result == (1, 2)


def test_close_based_fakeout_reclaim_none_without_reclaim():
    level = Decimal("1.1000")
    bars = [
        StructureBar(index=0, symbol="E", timeframe="M15", open_time=NOW, close_time=NOW, open=Decimal("1.1005"), high=Decimal("1.1006"), low=Decimal("1.0980"), close=Decimal("1.0985")),
    ]
    assert _close_based_fakeout_reclaim(bars, level, LiquiditySide.SELL_SIDE.value, lookback_bars=10) is None


def test_fakeout_quality_score_prefers_small_fakeouts():
    assert _fakeout_quality_score(0.2) == 100.0
    assert _fakeout_quality_score(3.5) == 0.0
    assert _fakeout_quality_score(None) == 50.0
    assert _fakeout_quality_score(1.5) < _fakeout_quality_score(0.6)


# --------------------------------------------------------------------------- end-to-end (through StrategyContext) ---
def _ctx_for(rows: list[dict]):
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows,
                                  bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"))
    assert ctx is not None
    return ctx


def _uptrend_rows() -> list[dict]:
    rows: list[dict] = []
    price = 1.1000
    seq, price, i = _leg_rows(18, 0.0030, start_price=price)
    rows += seq
    seq, price, i = _leg_rows(8, -0.0015, start_price=price, start_index=i)
    rows += seq
    seq, price, i = _leg_rows(14, 0.0030, start_price=price, start_index=i)
    rows += seq
    seq, price, i = _leg_rows(8, -0.0012, start_price=price, start_index=i)
    rows += seq
    seq, price, i = _leg_rows(4, 0.0040, start_price=price, start_index=i)
    rows += seq
    seq, price, i = _leg_rows(4, -0.0015, start_price=price, start_index=i)
    rows += seq
    return rows


def test_evaluate_order_flow_reaches_a_reasoned_decision_on_a_trending_series():
    """Same convention test_wyckoff.py already established for hard-to-hand-engineer synthetic
    swing data: assert the decision path is internally consistent, not that this one specific
    hand-built series always clears every downstream floor. A trivial 'no HTF bias at all' or
    unreachable-code rejection would indicate a real bug; a structural rejection
    (zone/array/geometry) is an acceptable, expected outcome for arbitrary synthetic data."""
    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_order_flow(ctx)
    assert signal.strategy_id == "bsi"
    assert signal.strategy_family == "bsi"
    if signal.valid:
        assert signal.direction in {"LONG", "SHORT"}
        if signal.direction == "LONG":
            assert signal.stop_loss < signal.proposed_entry < signal.take_profit
        else:
            assert signal.take_profit < signal.proposed_entry < signal.stop_loss
        assert signal.reward_risk is not None and signal.reward_risk >= 1.5
        assert signal.evidence["setup_subtype"] == "bsi_order_flow"
    else:
        assert signal.rejection_reason not in {None, ""}


def test_reactionary_mode_does_not_require_price_in_the_first_array(monkeypatch):
    """Regression for the reactionary chicken-and-egg bug (BSI Mentor Audit, 2026-09-02): array 1
    is only a historical precursor for reactionary mode, never itself the entry trigger --
    reactionary confirmation only exists because price already moved AWAY from array 1 to form a
    fresh, later, disjoint array 2. Requiring the SAME current-price snapshot to sit inside array 1
    (the pre-existing, unconditional check) before reactionary confirmation was even attempted made
    bsi_reactionary structurally unable to fire in real data: array 2's price_in_array check
    (further below) would then also need the identical price to sit inside a DIFFERENT, disjoint
    zone. This isolates exactly that gate via monkeypatching every other internal step to a fixed,
    valid stub, and asserts the fix: price outside array 1, inside array 2 -> valid signal."""
    import backend.mt5_strategies.families.bsi_engine as eng

    rows = _uptrend_rows()
    ctx = replace(_ctx_for(rows), htf_trend_h4="bullish")
    array1 = eng._EntryArray(kind="fvg", low=Decimal("1.0500"), high=Decimal("1.0510"), fvg_id="fvg1", size_atr=0.2, ref_bar_index=10)
    array2 = eng._EntryArray(kind="fvg", low=Decimal("1.1900"), high=Decimal("1.1910"), fvg_id="fvg2", size_atr=0.2, ref_bar_index=40)
    fake_price = 1.1905  # inside array2, well outside array1

    monkeypatch.setattr(eng, "_spread_within_safety_buffer", lambda ctx: True)
    monkeypatch.setattr(eng, "_current_price", lambda ctx: fake_price)
    monkeypatch.setattr(eng, "_zone_favorable", lambda direction, low, high, price: True)
    monkeypatch.setattr(eng, "_leg_bounds", lambda ctx, brk: (Decimal("1.0000"), Decimal("1.2000"), object()))
    monkeypatch.setattr(eng, "_latest_break", lambda ctx, **kw: _break(direction="bullish", bar_index=5, broken_level=1.05, break_price=1.06))
    monkeypatch.setattr(eng, "_select_entry_array", lambda ctx, direction, leg_low, leg_high, bars: array1)
    monkeypatch.setattr(eng, "_reactionary_confirmation", lambda ctx, direction, first_array: array2)
    monkeypatch.setattr(eng, "_mentor_stop", lambda ctx, direction, entry, array, atr, **kw: (Decimal("1.1800"), ""))
    monkeypatch.setattr(eng, "_opposing_structural_level", lambda ctx, direction: Decimal("1.2100"))
    monkeypatch.setattr(eng, "_liquidity_sweep_precedes", lambda ctx, **kw: False)

    signal = eng.evaluate_bsi_order_flow(ctx, entry_confirmation_mode="reactionary")
    assert signal.valid is True, signal.rejection_reason
    assert signal.direction == "LONG"
    assert signal.strategy_id == "bsi_reactionary"
    assert signal.evidence["entry_array_fvg_id"] == "fvg2"  # confirms array2, not array1, is the actual entry zone


def test_direct_mode_still_requires_price_inside_the_entry_array(monkeypatch):
    """Confirms the reactionary-mode fix above does not loosen direct mode's own array-1 gate --
    array 1 remains the real entry zone for direct (and ob_liquidity) modes."""
    import backend.mt5_strategies.families.bsi_engine as eng

    rows = _uptrend_rows()
    ctx = replace(_ctx_for(rows), htf_trend_h4="bullish")
    array1 = eng._EntryArray(kind="fvg", low=Decimal("1.0500"), high=Decimal("1.0510"), fvg_id="fvg1", size_atr=0.2, ref_bar_index=10)

    monkeypatch.setattr(eng, "_spread_within_safety_buffer", lambda ctx: True)
    monkeypatch.setattr(eng, "_current_price", lambda ctx: 1.1905)  # outside array1
    monkeypatch.setattr(eng, "_zone_favorable", lambda direction, low, high, price: True)
    monkeypatch.setattr(eng, "_leg_bounds", lambda ctx, brk: (Decimal("1.0000"), Decimal("1.2000"), object()))
    monkeypatch.setattr(eng, "_latest_break", lambda ctx, **kw: _break(direction="bullish", bar_index=5, broken_level=1.05, break_price=1.06))
    monkeypatch.setattr(eng, "_select_entry_array", lambda ctx, direction, leg_low, leg_high, bars: array1)

    signal = eng.evaluate_bsi_order_flow(ctx, entry_confirmation_mode="direct")
    assert signal.valid is False
    assert signal.rejection_reason == "PRICE_NOT_IN_ENTRY_ZONE"


def test_evaluate_abc_reaches_a_reasoned_decision():
    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_abc(ctx)
    assert signal.strategy_id == "bsi"
    if signal.valid:
        assert signal.evidence["setup_subtype"] == "bsi_abc"
        assert signal.reward_risk is not None and signal.reward_risk >= 1.5
    else:
        assert signal.rejection_reason not in {None, ""}


def test_evaluate_asian_session_no_trade_without_a_lunch_window_sweep():
    rows = _flat_rows(80)
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_asian(ctx)
    assert signal.valid is False
    assert signal.rejection_reason == "NO_ASIAN_LUNCH_WINDOW_SWEEP"


def test_evaluate_new_york_session_no_trade_on_flat_market():
    rows = _flat_rows(80)
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_new_york(ctx)
    assert signal.valid is False
    assert signal.rejection_reason == "NO_SWING_SWEEP_IN_NY_WINDOW"


def test_evaluate_under_over_no_trade_without_multi_touch_level():
    rows = _flat_rows(80)
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_under_over(ctx)
    assert signal.valid is False
    assert signal.rejection_reason in {"NO_QUALIFYING_MULTI_TOUCH_LEVEL", "NO_CLOSE_BASED_FAKEOUT_RECLAIM"}


def test_evaluate_ny_0930_gated_on_clock_window():
    """Regardless of price action, ny_0930 must reject outside its literal 9:30-11:59 NY window --
    this fixture's bars are timestamped at 10:00 UTC (05:00 NY in winter), well outside it."""
    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_0930(ctx)
    assert signal.valid is False
    assert signal.rejection_reason == "OUTSIDE_930_1159_NY_WINDOW"


def test_evaluate_abcd_reaches_a_reasoned_decision():
    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi_abcd(ctx)
    assert signal.strategy_id == "bsi"
    if signal.valid:
        assert signal.evidence["fixed_rr"] == 2.0
    else:
        assert signal.rejection_reason not in {None, ""}


def test_dispatcher_returns_first_valid_subtype_or_a_reason():
    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi(ctx)
    assert signal.strategy_id in {"bsi"} or signal.strategy_id.startswith("bsi")
    if signal.valid:
        assert "setup_subtype" in signal.evidence
    else:
        assert signal.rejection_reason not in {None, ""}


# --------------------------------------------------------------------------------- registration ---
def test_bsi_registered_in_evaluators():
    assert "bsi" in EVALUATORS
    assert EVALUATORS["bsi"] is evaluate_bsi


def test_bsi_registered_but_disabled_by_default(monkeypatch):
    """Isolated from whatever environment this suite happens to run in (2026-09-01: BSI has since
    been deliberately live-activated via MT5_STRATEGY_ACTIVATION_BSI in the actual deployed
    container -- this test is about the CODED default, not whatever an operator has since
    overridden it to; see the dedicated note in this file's activation-gating test section for the
    full explanation). Explicitly clears the override so this test is deterministic regardless of
    which environment runs it."""
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_BSI", raising=False)
    assert "bsi" in STRATEGY_FAMILIES
    assert STRATEGY_FAMILIES["bsi"]["default_activation"] == DISABLED
    assert activation_status("bsi") == DISABLED


# ============================================================================================
# BSI Intelligence Migration -- per-subtype activation gating (2026-09-01). A SECOND, more
# granular gate nested UNDER the family-level one above -- still fully inert for live/DEMO
# evaluation today, since `activation_status("bsi")` itself is DISABLED and evaluate_bsi is
# therefore never even called by families/__init__.py::evaluate_all(). These tests exercise the
# mechanism in isolation.
# ============================================================================================
import pytest as _pytest  # local import matching this file's otherwise-implicit pytest usage via bare asserts elsewhere; needed here for pytest.raises/monkeypatch fixtures

from backend.mt5_strategies import circuit_breaker as _circuit_breaker
from backend.mt5_strategies.models import ACTIVE_MT5, SHADOW_MT5, bsi_subtype_activation_status


def _reset_circuit_breaker():
    _circuit_breaker.reset_all()


def test_bsi_subtype_defaults_to_disabled_for_every_real_subtype(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    for subtype in ("bsi_order_flow", "bsi_abc", "bsi_abcd", "bsi_asian", "bsi_new_york", "bsi_0930", "bsi_under_over", "bsi_reactionary", "bsi_ob_liquidity"):
        monkeypatch.delenv(f"BSI_SUBTYPE_ACTIVATION_{subtype.upper().removeprefix('BSI_')}", raising=False)
        assert bsi_subtype_activation_status(subtype) == DISABLED


def test_bsi_subtype_env_override_to_each_valid_state(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    assert bsi_subtype_activation_status("bsi_under_over") == ACTIVE_MT5

    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "SHADOW_MT5")
    assert bsi_subtype_activation_status("bsi_under_over") == SHADOW_MT5

    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "DISABLED")
    assert bsi_subtype_activation_status("bsi_under_over") == DISABLED


def test_bsi_subtype_env_var_naming_matches_the_exact_requested_form(monkeypatch: _pytest.MonkeyPatch):
    """BSI_SUBTYPE_ACTIVATION_<SUBTYPE-WITHOUT-ITS-OWN-bsi_-PREFIX> -- e.g.
    BSI_SUBTYPE_ACTIVATION_UNDER_OVER for subtype 'bsi_under_over', BSI_SUBTYPE_ACTIVATION_NEW_YORK
    for 'bsi_new_york' -- proven directly, not just asserted in a docstring."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_NEW_YORK", "ACTIVE_MT5")
    assert bsi_subtype_activation_status("bsi_new_york") == ACTIVE_MT5
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_ORDER_FLOW", "SHADOW_MT5")
    assert bsi_subtype_activation_status("bsi_order_flow") == SHADOW_MT5


def test_bsi_subtype_invalid_env_value_falls_back_to_default(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "NOT_A_REAL_VALUE")
    assert bsi_subtype_activation_status("bsi_under_over") == DISABLED


def test_bsi_subtype_unknown_subtype_id_fails_closed(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_MADE_UP", "ACTIVE_MT5")
    assert bsi_subtype_activation_status("bsi_made_up") == DISABLED


def test_bsi_subtype_circuit_breaker_overrides_env_override(monkeypatch: _pytest.MonkeyPatch):
    """A tripped circuit breaker must outrank even an explicit ACTIVE_MT5 override -- an
    operational malfunction can never be outranked by activation config, same guarantee
    activation_status() already gives legacy strategies."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    assert bsi_subtype_activation_status("bsi_under_over") == ACTIVE_MT5
    for _ in range(_circuit_breaker.CONSECUTIVE_ERROR_TRIP_THRESHOLD):
        _circuit_breaker.record_evaluation_error("bsi_under_over")
    assert _circuit_breaker.is_tripped("bsi_under_over") is True
    assert bsi_subtype_activation_status("bsi_under_over") == DISABLED
    _reset_circuit_breaker()


def test_evaluate_bsi_never_calls_a_disabled_subtypes_evaluator(monkeypatch: _pytest.MonkeyPatch):
    """The core live-path guarantee: a DISABLED subtype must never reach candidate generation.
    Proven by monkeypatching the real evaluator with one that would raise if ever called."""
    _reset_circuit_breaker()
    monkeypatch.delenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", raising=False)  # explicit DISABLED default
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")

    def _boom(ctx):
        raise AssertionError("DISABLED subtype's evaluator must never be called")

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _boom)

    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi(ctx)  # must not raise -- proves _boom was never invoked
    assert signal.valid is False
    assert signal.rejection_reason == "SUBTYPE_DISABLED_bsi_under_over"


def test_evaluate_bsi_calls_a_shadow_subtypes_evaluator_and_stamps_status(monkeypatch: _pytest.MonkeyPatch):
    """SHADOW_MT5 (unlike DISABLED) must still reach candidate generation -- exactly like a
    SHADOW_MT5 legacy strategy is still evaluated by evaluate_all() (see that function's own
    docstring: 'unlike SHADOW_MT5, still evaluated... just never executable'). The winning
    signal's evidence must carry the resolved subtype activation status for a future execution-
    layer consumer to read."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "SHADOW_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    # BSI Mentor Audit fix (2026-09-02): the repo's own .env now genuinely sets
    # BSI_CONFIDENCE_V1_GATE_ENABLED=true (deployed live), which python-dotenv loads into this
    # test process's environment same as any other env var -- this test's fake signal below has no
    # real evidence for the floor scorer to work with, so a live-true gate falls through to
    # BELOW_FLOOR and breaks this test's unrelated assertion. Explicitly disabled here so the test
    # is env-independent, same pattern already applied to other BSI/legacy tests this mission for
    # exactly this class of contamination -- not a production behavior change.
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "false")

    from backend.mt5_strategies.models import StrategySignal

    called = {"n": 0}

    def _fake_valid_signal(ctx):
        called["n"] += 1
        return StrategySignal(
            strategy_id="bsi", strategy_family="bsi", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
            timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=70.0,
            proposed_entry=1.1000, stop_loss=1.0980, take_profit=1.1040, reward_risk=2.0, regime="trending_up",
            evidence={"setup_subtype": "bsi_under_over"},
        )

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _fake_valid_signal)

    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi(ctx)
    assert called["n"] == 1  # SHADOW_MT5 subtype's evaluator WAS called, unlike DISABLED
    assert signal.valid is True
    assert signal.evidence["subtype_activation_status"] == SHADOW_MT5


def test_evaluate_bsi_calls_an_active_subtypes_evaluator(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    # Same .env contamination fix as test_evaluate_bsi_calls_a_shadow_subtypes_evaluator_and_stamps_status above.
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "false")

    from backend.mt5_strategies.models import StrategySignal

    def _fake_valid_signal(ctx):
        return StrategySignal(
            strategy_id="bsi", strategy_family="bsi", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
            timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=70.0,
            proposed_entry=1.1000, stop_loss=1.0980, take_profit=1.1040, reward_risk=2.0, regime="trending_up",
            evidence={"setup_subtype": "bsi_under_over"},
        )

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _fake_valid_signal)

    rows = _uptrend_rows()
    ctx = _ctx_for(rows)
    signal = evaluate_bsi(ctx)
    assert signal.valid is True
    assert signal.evidence["subtype_activation_status"] == ACTIVE_MT5


def test_all_subtypes_disabled_means_family_level_gate_makes_this_fully_moot_anyway(monkeypatch: _pytest.MonkeyPatch):
    """Belt-and-suspenders check on the actual safety property this whole mechanism relies on:
    even if every subtype activation env var were somehow misconfigured to ACTIVE_MT5 right now,
    the family itself defaults to DISABLED in code, so families/__init__.py::evaluate_all() never
    calls evaluate_bsi() at all UNLESS an operator has separately, deliberately raised the family
    gate too -- this second gate only ever matters once/if that first one is raised. Explicitly
    clears MT5_STRATEGY_ACTIVATION_BSI first: this test is about the CODED DEFAULT, which is
    deliberately independent of whatever a live, already-activated environment's own env vars
    happen to be set to at test-run time (see the dedicated note below this test about running
    this suite inside a container where BSI has been deliberately live-activated)."""
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_BSI", raising=False)
    assert STRATEGY_FAMILIES["bsi"]["default_activation"] == DISABLED
    assert activation_status("bsi") == DISABLED


# NOTE (2026-09-01): this whole test module, and several others in this suite, were written
# assuming no MT5_STRATEGY_ACTIVATION_<ID>/BSI_SUBTYPE_ACTIVATION_<SUBTYPE> env-var override is
# active in the process running the tests -- true for essentially every test run before tonight.
# It is NO LONGER true when this suite is run directly inside `openterminalui-backend-1` now that
# BSI has been deliberately live-activated (bsi family ACTIVE_MT5, bsi_under_over ACTIVE_MT5, all
# 11 legacy strategies DISABLED via real env-var overrides in that container's actual
# environment) -- confirmed directly this session: `test_bsi_registered_but_disabled_by_default`
# (pre-existing, not written this session) and `test_mt5_multi_strategy_optimization.py::test_
# malformed_signal_trips_circuit_breaker_and_blocks_further_evaluation` (also pre-existing, tests
# `ema_trend`, now genuinely DISABLED live) both fail when run in that specific container, for
# this exact reason, confirmed by direct investigation -- NOT a code regression from anything in
# this session's diff. The correct comprehensive fix (not applied here, out of this session's own
# scope -- touching test-suite-wide fixtures deserves its own deliberate change, not a drive-by
# edit while investigating something else) is a session-scoped autouse fixture that clears every
# MT5_STRATEGY_ACTIVATION_*/BSI_SUBTYPE_ACTIVATION_* env var before each test, so the suite is
# reliably neutral regardless of which container it happens to run inside.


# ============================================================================================
# BSI_CONFIDENCE_V1 floor gate (BSI Intelligence Migration Phase G-extension, 2026-09-01).
# Evidence-derived MINIMUM FLOOR (not a monotonic ranking preference -- see
# bsi_confidence_v1.py's own module docstring for why), scoped exclusively to bsi_under_over,
# default OFF (BSI_CONFIDENCE_V1_GATE_ENABLED unset), configurable threshold
# (BSI_CONFIDENCE_V1_FLOOR, default 79.7). These tests monkeypatch
# bsi_confidence_v1.score_live_signal directly (rather than constructing real thesis/evidence
# dicts that would produce an unpredictable exact score) so the floor boundary itself is tested
# precisely and deterministically.
# ============================================================================================
from backend.mt5_strategies.families import bsi_confidence_v1 as _bsi_confidence_v1_mod


def _fake_score_result(composite_score: float | None) -> dict:
    return {"confidence_version": "BSI_CONFIDENCE_V1", "composite_score": composite_score, "components": {}, "components_available": [], "components_missing": [], "weights": {}, "threshold": None, "threshold_calibrated": False}


def _valid_signal_factory(subtype_name: str):
    from backend.mt5_strategies.models import StrategySignal

    def _fn(ctx):
        return StrategySignal(
            strategy_id="bsi", strategy_family="bsi", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
            timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=70.0,
            proposed_entry=1.1000, stop_loss=1.0980, take_profit=1.1040, reward_risk=2.0, regime="trending_up",
            evidence={"setup_subtype": subtype_name}, metadata={"bsi_thesis": {"bsi_version": "BSI_BASELINE_V1"}},
        )
    return _fn


def test_confidence_floor_rejects_bsi_under_over_below_floor(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(79.69))  # just below the 79.7 default floor

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is False
    assert "BSI_CONFIDENCE_V1_BELOW_FLOOR" in signal.rejection_reason
    assert signal.evidence["bsi_confidence_v1_score"] == 79.69  # still stamped even though rejected -- shadow data collection continues


def test_confidence_floor_does_not_reject_at_or_above_floor(monkeypatch: _pytest.MonkeyPatch):
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(79.7))  # exactly at the floor

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is True
    assert signal.evidence["bsi_confidence_v1_score"] == 79.7


def test_confidence_floor_never_applies_to_other_subtypes(monkeypatch: _pytest.MonkeyPatch):
    """Directive's own explicit scope requirement: bsi_new_york showed pure noise (composite
    correlation 0.05) -- applying a floor there would fabricate confidence the evidence doesn't
    support. Proven directly: an artificially terrible score (0.0) on bsi_new_york must NOT be
    rejected even with the gate enabled."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_NEW_YORK", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_new_york")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(0.0))

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_new_york", _valid_signal_factory("bsi_new_york"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is True  # not gated -- bsi_new_york is out of scope for this floor
    assert signal.evidence["bsi_confidence_v1_score"] == 0.0  # still computed and stamped for shadow tracking


def test_confidence_score_stamped_for_shadow_tracking_even_when_gate_disabled(monkeypatch: _pytest.MonkeyPatch):
    """Default state (flag off): behaves identically to before this change for order-eligibility
    purposes, but confidence data still accumulates for every BSI candidate -- directive's own
    explicit 'compute-and-stamp-only (shadow), no rejection' requirement when the flag is false."""
    _reset_circuit_breaker()
    monkeypatch.delenv("BSI_CONFIDENCE_V1_GATE_ENABLED", raising=False)
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(10.0))  # a terrible score

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is True  # gate is OFF -- even a terrible score does not reject
    assert signal.evidence["bsi_confidence_v1_score"] == 10.0  # but shadow data collection still happened


def test_confidence_floor_threshold_is_configurable_via_env(monkeypatch: _pytest.MonkeyPatch):
    """BSI_CONFIDENCE_V1_FLOOR must be genuinely read from the environment, not hardcoded --
    required so a future recalibration can adjust it without a code change."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_FLOOR", "50.0")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(60.0))  # above the custom 50.0 floor, below the default 79.7

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is True  # 60.0 clears the CUSTOM 50.0 floor, even though it's below the default 79.7


def test_confidence_scoring_failure_fails_open_never_breaks_signal_generation(monkeypatch: _pytest.MonkeyPatch):
    """Scoring must never itself crash a signal -- a bug or missing data in the confidence layer
    must never take down candidate generation."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")

    def _boom(**kw):
        raise RuntimeError("simulated scoring failure")

    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", _boom)

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))  # must not raise
    assert signal.valid is True  # fails open -- scoring couldn't run, floor never applied
    assert "bsi_confidence_v1_score" not in (signal.evidence or {})


def test_none_composite_score_fails_open_not_rejected(monkeypatch: _pytest.MonkeyPatch):
    """bsi_under_over_floor_verdict's own explicit fail-open behavior for an unavailable score --
    proven end-to-end through evaluate_bsi(), not just at the unit level."""
    _reset_circuit_breaker()
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_UNDER_OVER", "ACTIVE_MT5")
    monkeypatch.setenv("BSI_SUBTYPE_ORDER", "bsi_under_over")
    monkeypatch.setenv("BSI_CONFIDENCE_V1_GATE_ENABLED", "true")
    monkeypatch.setattr(_bsi_confidence_v1_mod, "score_live_signal", lambda **kw: _fake_score_result(None))

    import backend.mt5_strategies.families.bsi_engine as bsi_mod
    monkeypatch.setitem(bsi_mod._SUBTYPE_EVALUATORS, "bsi_under_over", _valid_signal_factory("bsi_under_over"))

    signal = evaluate_bsi(_ctx_for(_uptrend_rows()))
    assert signal.valid is True
    assert signal.evidence["bsi_confidence_v1_score"] is None


def test_default_floor_constant_matches_the_evidence_derived_value():
    from backend.mt5_strategies.families.bsi_confidence_v1 import BSI_CONFIDENCE_V1_FLOOR_DEFAULT, BSI_CONFIDENCE_V1_GATED_SUBTYPE
    assert BSI_CONFIDENCE_V1_FLOOR_DEFAULT == 79.7
    assert BSI_CONFIDENCE_V1_GATED_SUBTYPE == "bsi_under_over"


# ============================================================================================
# Mentor "golden example" reconstructions (BSI Mentor Audit follow-up, 2026-09-01/02). Unlike
# every fixture above -- which is generic synthetic data built for BRANCH coverage of the coded
# rules -- each fixture below is a minimal, deterministic OHLC bar sequence purpose-built to
# encode the STRUCTURAL SEQUENCE a specific mentor_notes_*.md file describes (swing points,
# break, retracement/FVG, sweep, reclaim -- whatever that subtype's own rule requires), fed
# through the REAL organic detectors (build_strategy_context -> analyze_bars' own swing/break/
# FVG/liquidity-sweep/equal-level pipeline -- market_structure/{swings,structure,imbalance,
# liquidity}.py), then through the REAL, unmodified evaluator. No internal function is
# monkeypatched and no detector output is hand-injected (contrast the pre-existing
# test_reactionary_mode_does_not_require_price_in_the_first_array above, which deliberately
# monkeypatches internals because organic fractal-swing engineering for THAT specific
# chicken-and-egg regression was judged not worth the fragility for what that test needed to
# prove). The one sanctioned shortcut, already established by test_reactionary_mode/
# test_direct_mode_still above: dataclasses.replace(ctx, htf_trend_h4=...) to force the daily-
# bias gate, since organically engineering a SECOND, independent H4 swing trend on top of the
# already-precise M15 sequence would add no evidentiary value -- the M15 structural sequence
# itself (swings/break/FVG/premium-discount/liquidity) is always 100% organic detector output.
#
# Priority order per the mission brief: Under/Over, Order Flow, New York. These three are the
# ones for which the mentor notes give an unambiguous, concrete-enough structural sequence AND
# for which an organic bar fixture satisfying every real-detector gate in the actual evaluator
# was successfully constructed and verified this session (build -> run -> inspect the organic
# swings/breaks/imbalances -> iterate on the BARS, never loosen the assertions or the detector).
# The remaining six subtypes (ABC, ABCD, Asian Session, 9:30AM, Reactionary, OB Liquidity) are
# NOT included here -- see this session's final report for why: their mentor notes are also
# concrete enough in principle, but each layers at least one further organic constraint on top
# of Order Flow's own already-fragile break+leg+premium-discount+FVG/OB conjunction (a 3-leg
# ABC/ABCD geometry with a LOCATION-scoped internal break, a session-clock alignment stacked on
# top of the same FVG machinery, a SECOND confirmation array, or a fakeout-and-reclaim of the
# SAME order block) -- genuinely reconstructable, but not completed to a verified passing state
# within this session's budget. That is a distinct finding from "mentor note too vague" and is
# reported as such, not mislabeled NOT_RECONSTRUCTABLE.
# ============================================================================================
def _bsi_golden_ctx(rows: list[dict], *, bid: Decimal = Decimal("1.1010"), ask: Decimal = Decimal("1.1012"), spread: Decimal = Decimal("0.0002")):
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=bid, ask=ask, spread=spread)
    assert ctx is not None
    return ctx


def test_mentor_golden_example_under_over_three_touch_fakeout_reclaim():
    """mentor_notes_under_over.md, Example 1/2: 'you just want to identify a clear supply demand
    or support and resistance area... with AT LEAST THREE TOUCHES' + 'you want to wait for price
    to break through that support or resistance level, candle close, okay? So WICKS DO NOT
    MATTER' + 'price RECLAIMS the level... entry at the reclaim'. Literal transcript elements
    encoded: the >=3-touch requirement, close-based (not wick) fakeout, and reclaim-is-the-entry
    mechanic. APPROXIMATE, not a literal transcript reproduction: the mentor never states an
    exact bar-count/spacing for the three touches or the fakeout depth -- this fixture is my own
    reasonable encoding of that qualitative sequence (three organically-detected fractal swing
    lows at the same price, a bar whose CLOSE breaks below it, then a bar whose CLOSE reclaims
    it), not a quote-for-quote reconstruction of a specific on-screen chart.

    Built organically: three real fractal low swings (left_bars=right_bars=3) at the exact same
    price 1.0951 (the real detect_equal_levels() clustering promotes this to a 3-touch EQL pool,
    matching bsi_under_over's own MT5_BSI_UNDER_OVER_MIN_TOUCHES=3 default) -- NOT a hand-built
    LiquidityLevel object. A later bar's CLOSE (not wick) breaks below 1.0951, and the following
    bar's CLOSE reclaims it near the level, which is the fixture's current price. The target
    comes from a real, organically-detected, unswept EQH pool above price (a second cluster of
    fractal high swings) -- the REAL _opposing_structural_level() tier-1 lookup, not injected.
    """
    L = 1.0951

    def _valley(i0: int, bump: float) -> list[dict]:
        offs = [0.0030, 0.0020, 0.0010, 0.0000, 0.0010, 0.0020, 0.0030]
        out = []
        for k, d in enumerate(offs):
            extra = bump if d > 0 else 0.0
            mid = L + d + 0.0005 + extra
            o, c = (mid - 0.0002, mid + 0.0002) if k % 2 == 0 else (mid + 0.0002, mid - 0.0002)
            out.append(_row(i0 + k, o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
        return out

    rows: list[dict] = []
    i = 0
    baseline = L + 0.0040
    for v in range(3):  # three fractal-confirmed touches of the SAME support level
        rows += _valley(i, bump=0.0010 * v)  # shoulders vary so they never cluster into a competing EQH
        i += 7
        for k in range(8):
            mid = baseline + 0.0010 * v + 0.00003 * k
            rows.append(_row(i, mid - 0.00005, mid + 0.00005, mid - 0.00015, mid + 0.00005))
            i += 1
    peakoffs = [0.0000, 0.0015, 0.0030, 0.0060, 0.0030, 0.0015, 0.0000]
    peak_mid = L + 0.0200
    for k, d in enumerate(peakoffs):
        mid = peak_mid + d
        o, c = (mid - 0.0002, mid + 0.0002) if k < 3 else (mid + 0.0002, mid - 0.0002)
        rows.append(_row(i, o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
        i += 1
    for k in range(6):
        mid = peak_mid - 0.0030 * (k + 1)
        o, c = mid + 0.0001, mid - 0.0001
        rows.append(_row(i, o, max(o, c) + 0.0001, min(o, c) - 0.0001, c))
        i += 1
    rows.append(_row(i, 1.0949, 1.0950, 1.0943, 1.0945))  # close-based fakeout BELOW the level
    i += 1
    rows.append(_row(i, 1.0951, max(1.0951, 1.0953) + 0.0001, min(1.0951, 1.0953) - 0.0001, 1.0953))  # close-based reclaim
    i += 1

    ctx = _bsi_golden_ctx(rows)
    signal = evaluate_bsi_under_over(ctx)
    assert signal.valid is True, signal.rejection_reason
    assert signal.direction == "LONG"
    assert signal.evidence["setup_subtype"] == "bsi_under_over"
    assert signal.evidence["level_touch_count"] >= 3
    assert signal.reward_risk is not None and signal.reward_risk >= 1.5


def _order_flow_golden_bars() -> list[tuple[float, float, float, float]]:
    return [
        (1.0930, 1.0932, 1.0928, 1.0929), (1.0929, 1.0930, 1.0920, 1.0922), (1.0922, 1.0923, 1.0912, 1.0913),
        (1.0913, 1.0915, 1.0900, 1.0905),  # swing low #1 (leg start of the smaller/earlier structure)
        (1.0905, 1.0915, 1.0904, 1.0909), (1.0908, 1.0913, 1.0907, 1.0912), (1.0914, 1.0917, 1.0913, 1.0916),
        (1.0914, 1.0918, 1.0913, 1.0917), (1.0914, 1.0919, 1.0913, 1.0918), (1.0913, 1.0920, 1.0912, 1.0919),
        (1.0915, 1.0921, 1.0914, 1.0920), (1.0917, 1.0922, 1.0916, 1.0921), (1.0920, 1.0928, 1.0919, 1.0927),
        (1.0925, 1.0945, 1.0925, 1.0940), (1.0940, 1.0958, 1.0935, 1.0955), (1.0955, 1.0972, 1.0950, 1.0968),
        (1.0968, 1.0975, 1.0960, 1.0965),  # swing high #1 -- the level Order Flow's BOS will break
        (1.0965, 1.0972, 1.0961, 1.0970), (1.0970, 1.0973, 1.0964, 1.0968), (1.0968, 1.0974, 1.0966, 1.0972),
        (1.0972, 1.0974, 1.0967, 1.0971), (1.0971, 1.0974, 1.0970, 1.0973), (1.0973, 1.0974, 1.0972, 1.0973),
        (1.0974, 1.0975, 1.0973, 1.0975), (1.0975, 1.0976, 1.0974, 1.0975),
        (1.0980, 1.1002, 1.0978, 1.1000),  # displacement candle: CLOSES above swing high #1 -> real BOS
        (1.1000, 1.1002, 1.0985, 1.0990), (1.0990, 1.0992, 1.0970, 1.0975),  # mitigates the breakout candle's own FVG
        (1.0975, 1.0978, 1.0950, 1.0955), (1.0955, 1.0958, 1.0915, 1.0917),
        (1.0917, 1.0918, 1.0913, 1.0914), (1.0914, 1.0920, 1.0912, 1.0915), (1.0920, 1.0924, 1.0919, 1.0921),
        (1.0921, 1.0922, 1.09182, 1.09185),  # final bar: current price sits inside the discount FVG
    ]


def test_mentor_golden_example_order_flow_bos_discount_fvg_entry():
    """mentor_notes_order_flow.md, Example (1)/(2): uptrend -> price breaks a prior swing high
    (structure break) -> 'draw out your Fibonacci from the very top to the very low' of the
    breaking move -> discount zone (<0.5) -> 'there is no imbalance here so we will not consider
    this... take your entry from the MOST EXTREME zone' -> unmitigated FVG entry -> 'target the
    high... another liquidity pool'. Literal transcript elements encoded: continuation/BOS-style
    Order Flow ('the continuation of the trend'), the Fibonacci-anchored discount-zone gate, and
    TP at a further opposing liquidity pool. APPROXIMATE: the mentor never states exact swing
    prices, FVG size, or bar spacing -- this fixture is my own reasonable encoding of that
    sequence, not a literal reproduction of an on-screen chart.

    Built organically: two real fractal low swings (higher-low) and two real fractal high swings
    (higher-high) give the M15 bars their own genuine bullish trend.state (classify_trend, NOT
    forced) so the break of the first swing high is organically classified BOS by
    detect_structure_breaks() -- htf_trend_h4 is the ONE field forced via dataclasses.replace
    (see this section's own header comment for why). _leg_bounds() anchors the Fibonacci to the
    real preceding swing low and the break's own real break_price. A real, organically-detected,
    small unmitigated bullish FVG sits in the resulting discount zone and is the fixture's
    current price -- the deliberately larger FVG left by the breakout candle itself is confirmed
    MITIGATED (price closes back through it during the pullback) before the fixture's final bar,
    matching the mentor's own 'there's no imbalance here, it's all filled' rejection rule.
    """
    rows = [_row(i, o, h, l, c) for i, (o, h, l, c) in enumerate(_order_flow_golden_bars())]
    ctx = replace(_bsi_golden_ctx(rows, bid=Decimal("1.09183"), ask=Decimal("1.09187"), spread=Decimal("0.00004")), htf_trend_h4="bullish")
    signal = evaluate_bsi_order_flow(ctx)
    assert signal.valid is True, signal.rejection_reason
    assert signal.direction == "LONG"
    assert signal.evidence["setup_subtype"] == "bsi_order_flow"
    assert signal.evidence["structure_break_kind"] == "bos"
    assert signal.evidence["entry_array_kind"] == "fvg"
    assert signal.reward_risk is not None and signal.reward_risk >= 1.5


def test_order_flow_follows_daily_bias_not_h4_when_they_disagree():
    """BSI Daily Bias Audit wiring proof: reuses the exact same organic golden fixture above, but
    now with htf_trend_h4 and htf_trend_daily set to OPPOSITE directions -- proving the live gate
    genuinely follows ctx.htf_trend_daily now, not ctx.htf_trend_h4, rather than merely asserting
    it via source inspection. H4=bearish (would reject NO_HTF_BIAS-consistent-direction here since
    the fixture's own real M15 structure is bullish) + Daily=bullish (agrees with the real
    structure) -> must still fire, exactly as it did when H4 alone was bullish."""
    rows = [_row(i, o, h, l, c) for i, (o, h, l, c) in enumerate(_order_flow_golden_bars())]
    ctx = replace(_bsi_golden_ctx(rows, bid=Decimal("1.09183"), ask=Decimal("1.09187"), spread=Decimal("0.00004")), htf_trend_h4="bearish", htf_trend_daily="bullish")
    signal = evaluate_bsi_order_flow(ctx)
    assert signal.valid is True, signal.rejection_reason
    assert signal.direction == "LONG"


def test_order_flow_rejects_when_daily_bias_disagrees_even_though_h4_agrees():
    """The mirror case: H4=bullish (agrees with the real M15 structure -- would have fired under
    the OLD, pre-audit gate) but Daily=bearish (mentor-faithful HTF source now disagrees) -> must
    reject NO_HTF_BIAS-equivalent (specifically, htf_direction not in bullish/bearish's own
    downstream direction-derivation makes this fall through to a structural rejection since the
    trade direction no longer matches the fixture's own bullish structure), proving Daily
    genuinely CONTROLS the outcome in both directions, not just when it happens to agree."""
    rows = [_row(i, o, h, l, c) for i, (o, h, l, c) in enumerate(_order_flow_golden_bars())]
    ctx = replace(_bsi_golden_ctx(rows, bid=Decimal("1.09183"), ask=Decimal("1.09187"), spread=Decimal("0.00004")), htf_trend_h4="bullish", htf_trend_daily="bearish")
    signal = evaluate_bsi_order_flow(ctx)
    # direction is now forced SHORT (from daily=bearish) against a fixture whose real M15
    # structure only supports a bullish break/leg/entry-array sequence -- must not fire as LONG,
    # and in practice fails a downstream structural gate (no bearish break/array exists here).
    assert not (signal.valid and signal.direction == "LONG")


def test_mentor_golden_example_new_york_session_sweep_retest():
    """mentor_notes_ny_session.md, main lesson: 'identify a key high and a key pullback' ->
    price sweeps that key level -> 'wait for price to come back down and retest the level of the
    very high [low] which got swept' -> entry at the retest -> 'I always and I mean always go
    for one to two risk reward on this trading strategy'. Precise timing rule, also literally
    encoded: 'price must sweep out the significant high/low during the New York session... it
    must, and I mean must, happen in the New York session'. APPROXIMATE: the mentor never states
    an exact fakeout depth or bar count for 'a significant pullback' -- this fixture is my own
    reasonable encoding, not a literal reproduction of an on-screen chart.

    Built organically: a real fractal swing low (the 'key low') confirms several bars before the
    sweep. The sweep itself is a single real bar whose WICK breaches the swept level and whose
    CLOSE reclaims it -- detect_liquidity_sweeps()' own real sweep definition, not injected --
    timestamped (via the fixture's own bar clock) so its confirmation_time falls inside the real
    13:30-16:00 UTC New York window bsi_new_york's own _in_ny_session_window() checks. That same
    bar is also the fixture's current price, so the code's own retest-tolerance check (price near
    the sweep's own wick extreme) is satisfied by construction, exactly like a live cycle
    evaluating the sweep bar itself as 'now'.
    """
    L = 1.0951
    template: list[tuple] = []
    for k in range(14):
        mid = L + 0.0040 + 0.00002 * k
        o, c = mid - 0.0001, mid + 0.0001
        template.append((o, max(o, c) + 0.0001, min(o, c) - 0.0001, c))
    offs = [0.0030, 0.0020, 0.0010, 0.0000, 0.0010, 0.0020, 0.0030]
    for k, d in enumerate(offs):
        mid = L + d + 0.0003
        o, c = (mid - 0.0002, mid + 0.0002) if k % 2 == 0 else (mid + 0.0002, mid - 0.0002)
        template.append((o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))  # fractal low swing at 1.0951 (the "key low")
    for k in range(6):
        mid = L + 0.0040 + 0.00002 * k
        o, c = mid - 0.0001, mid + 0.0001
        template.append((o, max(o, c) + 0.0001, min(o, c) - 0.0001, c))
    template.append((1.0951, 1.09525, 1.09485, 1.0952))  # the sweep bar itself: wick below 1.0951, close reclaims it

    n = len(template)
    target_close = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)  # 09:00 America/New_York -- inside the NY morning window
    start = target_close - timedelta(minutes=15 * (n - 1)) - timedelta(minutes=15)
    rows = []
    for idx, (o, h, l, c) in enumerate(template):
        t = start + timedelta(minutes=15 * idx)
        rows.append({"time": t.isoformat(), "open": o, "high": h, "low": l, "close": c, "tick_volume": 500, "spread": 2})

    ctx = _bsi_golden_ctx(rows)
    signal = evaluate_bsi_new_york(ctx)
    assert signal.valid is True, signal.rejection_reason
    assert signal.direction == "LONG"
    assert signal.evidence["setup_subtype"] == "bsi_new_york"
    assert signal.evidence["fixed_rr"] == 2.0
    assert signal.reward_risk == 2.0


def test_every_registered_bsi_subtype_is_reachable_from_evaluate_bsi_dispatch():
    """Regression test for the 2026-09-02 candidate-generation starvation bug: bsi_reactionary
    and bsi_ob_liquidity were fully coded, registered in _SUBTYPE_EVALUATORS, and individually
    ACTIVE_MT5 via BSI_SUBTYPE_ACTIVATION_* -- but evaluate_bsi()'s dispatch loop only ever
    iterates BSI_SUBTYPE_ORDER (env) or, absent that override, _DEFAULT_SUBTYPE_ORDER, and the
    latter simply never listed them. Activation state alone said nothing about reachability --
    a subtype missing from the order tuple is never even looked up, regardless of its activation
    status. This test fails the moment a future subtype is added to _SUBTYPE_EVALUATORS (the
    registry evaluate_bsi() actually calls .get() against) without also being added to
    _DEFAULT_SUBTYPE_ORDER (the registry evaluate_bsi() actually iterates)."""
    from backend.mt5_strategies.families.bsi_engine import _DEFAULT_SUBTYPE_ORDER, _SUBTYPE_EVALUATORS

    registered = set(_SUBTYPE_EVALUATORS)
    dispatched = set(_DEFAULT_SUBTYPE_ORDER)
    missing = registered - dispatched
    assert not missing, f"registered BSI subtype(s) unreachable from evaluate_bsi() dispatch: {sorted(missing)}"
    # Every dispatch-order entry must also resolve to a real evaluator -- a typo'd or stale name
    # in _DEFAULT_SUBTYPE_ORDER silently no-ops (evaluator = None -> skipped) rather than erroring.
    dangling = dispatched - registered
    assert not dangling, f"_DEFAULT_SUBTYPE_ORDER references unregistered subtype(s): {sorted(dangling)}"
    # Pin the exact expected 9 so this test also catches an accidental subtype removal, not just
    # a future addition.
    expected = {
        "bsi_order_flow", "bsi_abc", "bsi_asian", "bsi_new_york", "bsi_under_over",
        "bsi_0930", "bsi_abcd", "bsi_reactionary", "bsi_ob_liquidity",
    }
    assert registered == expected
    assert dispatched == expected

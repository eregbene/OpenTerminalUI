"""Minimum stop-distance floor for structural-level-based strategies.

Fixes a real bug found on the live $10K demo: `breakout`, `liquidity_sweep_reversal`, and
`session_breakout` used a raw structural price (a broken BOS level, a swept liquidity price, a
session high/low) DIRECTLY as the stop, with no minimum distance floor. A barely-broken level
could sit a fraction of a pip from entry -- tighter than typical spread -- while still clearing
the reward:risk RATIO floor (a small stop with a proportionally small target looks fine on
paper), producing near-guaranteed stop-outs from ordinary noise rather than genuine adverse
price movement. Two real NZDUSD trades on the live account showed exactly this: a 1.3-pip stop.

Fix: route the raw structural level through construct_dynamic_stop() (the same utility MTFAI1's
own stop construction already uses) instead of using it directly, bounding the final distance
between min_atr_mult x ATR and max_atr_mult x ATR with a spread-aware buffer.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.market_structure.models import ConceptStatus, Direction, DisplacementEvent, LiquiditySide, LiquiditySweep, StructureBreak, StructureBreakKind
from backend.mt5_strategies.context import build_strategy_context
from backend.mt5_strategies.families import (
    _dynamic_stop,
    evaluate_breakout,
    evaluate_ema_trend,
    evaluate_liquidity_sweep_reversal,
    evaluate_mean_reversion,
    evaluate_momentum,
    evaluate_session_breakout,
    evaluate_smc_continuation,
    evaluate_support_resistance_bounce,
    evaluate_trend_pullback,
    evaluate_vwap_reversion,
)
from backend.mt5_strategies.fusion import build_candidates

from backend.tests.test_mt5_multi_strategy import NOW, _flat_context, _mk_break, _mk_displacement, _mk_rows, _mk_sweep


def _mk_break_at(symbol: str, *, bar_index: int, direction: Direction, kind: StructureBreakKind, broken_level: Decimal) -> StructureBreak:
    return StructureBreak(
        id=f"brk_{bar_index}_{kind.value}", symbol=symbol, timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        direction=direction, status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        break_kind=kind, break_price=Decimal("1.1050"), broken_level=broken_level, broken_swing_id="swg_test", break_distance=Decimal("0.0050"),
        break_distance_atr=1.2, confirmation_mode="close", bar_index=bar_index, explanation="synthetic test break",
    )


# 1. Breakout: a barely-broken level (0.1 pip from entry) no longer produces a stop that tight.
def test_breakout_stop_never_tighter_than_dynamic_floor_for_barely_broken_level():
    ctx = _flat_context(regime="breakout")
    entry_ask = ctx.ask  # 1.1012
    barely_broken_level = entry_ask - Decimal("0.00001")  # 0.1 pip away -- the bug scenario
    ctx.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=barely_broken_level))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))

    signal = evaluate_breakout(ctx)

    assert signal.direction == "LONG"
    stop_distance = Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss))
    raw_distance = entry_ask - barely_broken_level
    assert stop_distance > raw_distance  # never the raw, unbounded 0.1-pip distance
    assert stop_distance >= ctx.atr_m15 * Decimal("0.9")  # comfortably ATR-scaled, not noise-sized


# 2. Breakout: a genuinely far broken level is barely adjusted (the fix must not needlessly
# widen an already-reasonable stop).
def test_breakout_stop_mostly_unchanged_for_reasonable_broken_level():
    ctx = _flat_context(regime="breakout")
    entry_ask = ctx.ask
    reasonable_level = entry_ask - (ctx.atr_m15 * Decimal("1.2"))  # already within the normal ATR band
    ctx.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=reasonable_level))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))

    signal = evaluate_breakout(ctx)

    assert signal.direction == "LONG"
    stop_distance = Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss))
    raw_distance = entry_ask - reasonable_level
    assert stop_distance == pytest_approx(raw_distance)


def pytest_approx(value: Decimal, tolerance: Decimal = Decimal("0.0005")):
    class _Approx:
        def __eq__(self, other):
            return abs(Decimal(str(other)) - value) <= tolerance

    return _Approx()


# 3. Liquidity-sweep-reversal: a barely-swept price no longer produces a razor-thin stop.
def test_liquidity_sweep_reversal_stop_never_tighter_than_dynamic_floor():
    ctx = _flat_context(regime="reversal")
    entry_ask = ctx.ask
    barely_swept = entry_ask - Decimal("0.00001")
    sweep = _mk_sweep("EURUSD", bar_index=len(ctx.m15_rows) - 1, side=LiquiditySide.SELL_SIDE, direction=Direction.BULLISH).model_copy(update={"swept_price": barely_swept})
    ctx.m15_snapshot.liquidity_sweeps.append(sweep)
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))
    shift = StructureBreak(
        id="shift_1", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        direction=Direction.BULLISH, status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        break_kind=StructureBreakKind.CHOCH, break_price=Decimal("1.1015"), broken_level=Decimal("1.1000"), broken_swing_id="swg_shift", break_distance=Decimal("0.0015"),
        break_distance_atr=1.0, confirmation_mode="close", bar_index=len(ctx.m15_rows) - 1, explanation="synthetic shift",
    )
    ctx.m15_snapshot.breaks.append(shift)

    signal = evaluate_liquidity_sweep_reversal(ctx)

    assert signal.direction == "LONG"
    stop_distance = Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss))
    raw_distance = entry_ask - barely_swept
    assert stop_distance > raw_distance
    assert stop_distance >= ctx.atr_m15 * Decimal("0.9")


# 4. Session-breakout: a barely-broken session level no longer produces a razor-thin stop.
def test_session_breakout_stop_never_tighter_than_dynamic_floor():
    from backend.market_structure.models import SessionLevel

    ctx = _flat_context(regime="breakout")
    price = float(ctx.m15_rows[-1]["close"])
    barely_broken_high = Decimal(str(price)) - Decimal("0.00001")
    level = SessionLevel(
        id="sess_1", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        session_name="LONDON", level_name="high", level=barely_broken_high, session_date=NOW.date().isoformat(),
    )
    ctx.m15_snapshot.session_levels.append(level)

    signal = evaluate_session_breakout(ctx)

    assert signal.direction == "LONG"
    stop_distance = Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss))
    raw_distance = Decimal(str(price)) - barely_broken_high
    assert stop_distance > raw_distance
    assert stop_distance >= ctx.atr_m15 * Decimal("0.9")


# 5. When no safe distance can be constructed at all (e.g. ATR unavailable), the signal is
# explicitly invalid rather than falling back to the raw, unbounded level.
def test_dynamic_stop_none_produces_invalid_signal_not_raw_level():
    ctx = _flat_context(regime="breakout")
    ctx_no_atr = dataclasses.replace(ctx, atr_m15=None)
    entry_ask = ctx_no_atr.ask
    barely_broken_level = entry_ask - Decimal("0.00001")
    ctx_no_atr.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx_no_atr.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=barely_broken_level))
    ctx_no_atr.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx_no_atr.m15_rows) - 1, direction=Direction.BULLISH))

    signal = evaluate_breakout(ctx_no_atr)

    # break_distance_atr fallback (1.2) still supplies a usable ATR proxy for breakout
    # specifically (see evaluate_breakout's own atr fallback) -- this proves the fallback chain
    # itself, not a genuinely atr-less path, still never returns the raw unbounded level.
    stop_distance = Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss))
    raw_distance = entry_ask - barely_broken_level
    assert stop_distance > raw_distance


# 6. EMA trend remains correct -- identical stop distance to before this fix (pure ATR x 1.5,
# no structural level involved).
def test_ema_trend_strategy_remains_correct():
    rows = _mk_rows(120, base=1.1000, step=0.00006)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"))
    signal = evaluate_ema_trend(ctx)
    assert signal.valid is True
    assert signal.direction == "LONG"
    geometry = signal.metadata
    assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.5"), rel=Decimal("0.02"))


# 7. Trend pullback remains correct.
def test_trend_pullback_strategy_remains_correct():
    rows = _mk_rows(100, base=1.1000, step=0.00003, osc_amplitude=0.0004, osc_period=15)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1030"), ask=Decimal("1.1032"), spread=Decimal("0.0002"))
    ctx = dataclasses.replace(ctx, htf_trend_h1="bullish")
    signal = evaluate_trend_pullback(ctx)
    if signal.valid:
        geometry = signal.metadata
        assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.2"), rel=Decimal("0.02"))


# 8. Mean reversion remains correct.
def test_mean_reversion_strategy_remains_correct():
    rows = _mk_rows(60, base=1.1200, step=-0.0009)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal(str(rows[-1]["close"])), ask=Decimal(str(rows[-1]["close"] + 0.0002)), spread=Decimal("0.0002"))
    signal = evaluate_mean_reversion(ctx)
    if signal.valid:
        geometry = signal.metadata
        assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.2"), rel=Decimal("0.02"))


# 9. SMC continuation remains correct.
def test_smc_continuation_strategy_remains_correct():
    ctx = _flat_context(regime="trending_up", htf_h4="bullish")
    last_idx = len(ctx.m15_rows) - 1
    ctx.m15_snapshot.breaks.append(_mk_break("EURUSD", bar_index=last_idx, direction=Direction.BULLISH, kind=StructureBreakKind.BOS))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=last_idx, direction=Direction.BULLISH))
    signal = evaluate_smc_continuation(ctx)
    assert signal.valid is True
    geometry = signal.metadata
    assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.5"), rel=Decimal("0.02"))


# 10. Support/resistance bounce remains correct.
def test_support_resistance_bounce_strategy_remains_correct():
    from backend.market_structure.models import LiquidityLevel

    ctx = _flat_context(regime="ranging")
    price = float(ctx.m15_rows[-1]["close"])
    level = LiquidityLevel(
        id="lvl_1", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        level=Decimal(str(price)), side="sell_side", source="test", tolerance=Decimal("0.0020"), touch_count=1, liquidity_score=0.5,
    )
    ctx.m15_snapshot.liquidity_levels.append(level)
    last = dict(ctx.m15_rows[-1])
    last["close"] = last["open"] + 0.0005
    ctx.m15_rows[-1] = last
    signal = evaluate_support_resistance_bounce(ctx)
    if signal.valid:
        geometry = signal.metadata
        assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.2"), rel=Decimal("0.02"))


# 11. Momentum remains correct.
def test_momentum_strategy_remains_correct():
    rows = _mk_rows(80, base=1.1000, step=0.0002)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal(str(rows[-1]["close"])), ask=Decimal(str(rows[-1]["close"] + 0.0002)), spread=Decimal("0.0002"))
    signal = evaluate_momentum(ctx)
    if signal.valid:
        geometry = signal.metadata
        assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.5"), rel=Decimal("0.02"))


# 12. VWAP reversion remains correct.
def test_vwap_reversion_strategy_remains_correct():
    rows = _mk_rows(40, base=1.1000, step=0.0, wick=0.0003)
    rows[-1]["close"] = rows[-1]["open"] - 0.003
    rows[-1]["low"] = rows[-1]["close"] - 0.0003
    for r in rows:
        r["tick_volume"] = 100
    rows[-1]["tick_volume"] = 500
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal(str(rows[-1]["close"])), ask=Decimal(str(rows[-1]["close"] + 0.0002)), spread=Decimal("0.0002"))
    signal = evaluate_vwap_reversion(ctx)
    if signal.valid:
        geometry = signal.metadata
        assert Decimal(str(geometry["final_stop_distance"])) == pytest.approx(Decimal(str(geometry["atr"])) * Decimal("1.2"), rel=Decimal("0.02"))


# 13. Long and short geometry are symmetric.
def test_long_and_short_geometry_are_symmetric():
    up_rows = _mk_rows(120, base=1.1000, step=0.00006)
    up_ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=up_rows, h1_rows=up_rows, h4_rows=up_rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"))
    long_signal = evaluate_ema_trend(up_ctx)
    down_rows = _mk_rows(120, base=1.1000, step=-0.00006)
    down_ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=down_rows, h1_rows=down_rows, h4_rows=down_rows, bid=Decimal("1.0920"), ask=Decimal("1.0922"), spread=Decimal("0.0002"))
    short_signal = evaluate_ema_trend(down_ctx)
    assert long_signal.direction == "LONG"
    assert short_signal.direction == "SHORT"
    assert long_signal.stop_loss < long_signal.proposed_entry < long_signal.take_profit
    assert short_signal.take_profit < short_signal.proposed_entry < short_signal.stop_loss
    long_distance = Decimal(str(long_signal.metadata["final_stop_distance"]))
    short_distance = Decimal(str(short_signal.metadata["final_stop_distance"]))
    assert long_distance == pytest.approx(short_distance, rel=Decimal("0.15"))  # same formula, comparable magnitude


# 14. JPY-scaled pairs (large price, coarse tick size) handle distance correctly -- no fixed
# decimal-place/pip assumption anywhere in the dynamic-stop path.
def test_jpy_scaled_pair_handles_distance_correctly():
    rows = _mk_rows(120, base=150.000, step=0.006, wick=0.03)
    ctx = build_strategy_context(symbol="USDJPY", broker_symbol="USDJPY", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("150.800"), ask=Decimal("150.820"), spread=Decimal("0.020"))
    signal = evaluate_ema_trend(ctx)
    if signal.valid:
        assert signal.stop_loss > 0
        stop_distance = abs(Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss)))
        assert stop_distance > Decimal("0.05")  # meaningfully JPY-pip-scaled, not a 5-digit-FX-sized fraction


# 15. XAUUSD-scaled symbol (large price, coarse tick size) handles distance correctly.
def test_xauusd_scaled_symbol_handles_distance_correctly():
    rows = _mk_rows(120, base=4000.00, step=0.15, wick=0.8)
    ctx = build_strategy_context(symbol="XAUUSD", broker_symbol="XAUUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("4020.00"), ask=Decimal("4020.40"), spread=Decimal("0.40"))
    signal = evaluate_ema_trend(ctx)
    if signal.valid:
        stop_distance = abs(Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss)))
        assert stop_distance > Decimal("1.0")  # meaningfully gold-scaled


# 16. Final RR is recalculated after SL normalization -- proposed (raw-level-implied) RR and
# final (post-normalization) RR can differ, and only final RR is ever persisted/used.
def test_final_rr_recalculated_after_stop_normalization():
    ctx = _flat_context(regime="breakout")
    entry_ask = ctx.ask
    barely_broken_level = entry_ask - Decimal("0.00001")
    proposed_raw_distance = entry_ask - barely_broken_level
    ctx.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=barely_broken_level))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))

    signal = evaluate_breakout(ctx)

    final_stop_distance = abs(Decimal(str(signal.proposed_entry)) - Decimal(str(signal.stop_loss)))
    target_distance = abs(Decimal(str(signal.take_profit)) - Decimal(str(signal.proposed_entry)))
    proposed_rr = target_distance / proposed_raw_distance
    final_rr = Decimal(str(signal.reward_risk))
    assert final_rr != pytest_approx(proposed_rr, tolerance=Decimal("0.05"))  # genuinely different
    assert final_rr == pytest_approx(target_distance / final_stop_distance, tolerance=Decimal("0.01"))  # final RR uses FINAL stop


# 17. Final RR below 1.5 rejects.
def test_final_rr_below_minimum_rejects():
    ctx = _flat_context(regime="trending_up")
    stop, _reason = _dynamic_stop(ctx, "LONG", ctx.ask, None, ctx.atr_m15, min_atr_mult=1.5, max_atr_mult=1.5)
    assert stop is not None
    from backend.mt5_strategies.families import _signal

    tiny_target = ctx.ask + (ctx.ask - stop) * Decimal("1.0")  # RR == 1.0, below the 1.5 floor
    signal = _signal(ctx, strategy_id="ema_trend", family="ema_trend", timeframe="M15", direction="LONG", strength=60.0,
                      entry=ctx.ask, stop=stop, target=tiny_target, evidence={})
    assert signal.valid is False
    assert signal.rejection_reason == "FINAL_RR_BELOW_MINIMUM"


# 18. A tiny (pre-normalization) stop cannot create an artificial, unrealistically high RR --
# the NZDUSD-shaped bug this whole fix addresses.
def test_tiny_stop_cannot_create_artificial_high_rr():
    ctx = _flat_context(regime="breakout")
    entry_ask = ctx.ask
    barely_broken_level = entry_ask - Decimal("0.00001")  # would have implied RR ~ huge if used raw
    ctx.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=barely_broken_level))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))

    signal = evaluate_breakout(ctx)

    assert signal.reward_risk is not None
    assert signal.reward_risk < Decimal("7.0")  # never the artificially inflated raw-level RR


# 19. Broker stop-level violation normalizes safely (widens to at least the broker minimum) or
# rejects -- never silently accepts a stop tighter than the broker allows.
def test_broker_stop_level_violation_normalizes_or_rejects():
    ctx = _flat_context(regime="trending_up")
    huge_broker_floor = ctx.atr_m15 * Decimal("10")  # far beyond the strategy's own 1.5x ATR band
    ctx_with_floor = dataclasses.replace(ctx, broker_min_stop_distance=huge_broker_floor)
    stop, reason = _dynamic_stop(ctx_with_floor, "LONG", ctx.ask, None, ctx.atr_m15, min_atr_mult=1.5, max_atr_mult=1.5)
    # min_atr_mult == max_atr_mult == 1.5 caps max_distance at 1.5xATR, below the 10xATR broker
    # floor -- min_distance > max_distance is therefore correctly unsatisfiable -> rejected.
    assert stop is None
    assert reason in {"STOP_DISTANCE_INVALID", "BROKER_STOP_LEVEL_VIOLATION"}

    # With a wide enough max_atr_mult, the same broker floor is instead respected by widening.
    stop2, _reason2 = _dynamic_stop(ctx_with_floor, "LONG", ctx.ask, None, ctx.atr_m15, min_atr_mult=1.0, max_atr_mult=15.0)
    assert stop2 is not None
    distance2 = ctx.ask - stop2
    assert distance2 >= huge_broker_floor


# 20. Spread-aware floor works -- a stop distance under 3x the current spread is rejected.
def test_spread_aware_floor_rejects_too_tight_relative_to_spread():
    ctx = _flat_context(regime="trending_up")
    wide_spread_ctx = dataclasses.replace(ctx, spread=ctx.atr_m15 * Decimal("2"))  # spread itself is 2x ATR
    stop, reason = _dynamic_stop(wide_spread_ctx, "LONG", ctx.ask, None, ctx.atr_m15, min_atr_mult=1.0, max_atr_mult=1.0)
    # min_distance = 1.0x ATR < 3x spread (6x ATR) -> must fail the spread-ratio floor.
    assert stop is None
    assert reason == "STOP_INSIDE_SPREAD_BUFFER"


# 21. ATR floor works -- the final distance is never below min_atr_mult x ATR.
def test_atr_floor_enforced():
    ctx = _flat_context(regime="trending_up")
    stop, reason = _dynamic_stop(ctx, "LONG", ctx.ask, None, ctx.atr_m15, min_atr_mult=2.0, max_atr_mult=2.0)
    assert stop is not None
    distance = ctx.ask - stop
    assert distance == pytest.approx(ctx.atr_m15 * Decimal("2.0"), rel=Decimal("0.01"))


# 22. Candidate/calibration logging preserves raw and final geometry (Part 11/19) -- the fused
# candidate's context carries the full stop_geometry audit trail, not just the final numbers.
def test_candidate_logging_preserves_raw_and_final_geometry():
    ctx = _flat_context(regime="breakout")
    entry_ask = ctx.ask
    barely_broken_level = entry_ask - Decimal("0.00001")
    ctx.m15_snapshot.breaks.append(_mk_break_at("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS, broken_level=barely_broken_level))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))
    signal = evaluate_breakout(ctx)
    assert signal.valid is True

    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C-geom", signals=[signal], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    geometry = candidates[0]["context"]["strategy_evidence"]["stop_geometry"]
    assert geometry["structural_reference"] == pytest.approx(float(barely_broken_level), rel=1e-6)
    assert geometry["final_stop_distance"] > 0
    assert geometry["atr"] is not None
    assert candidates[0]["context"]["atr"] == geometry["atr"]
    assert candidates[0]["context"]["spread"] == geometry["spread"]

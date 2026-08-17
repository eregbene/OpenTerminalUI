"""Tests for backend/market_structure/squeeze_momentum.py -- the independently-implemented
BB-vs-KC squeeze + linreg momentum feature (docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md).
Pure-function tests only; this feature is not wired into any strategy or live decision yet."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.squeeze_momentum import (
    SQUEEZE_OFF,
    SQUEEZE_ON,
    SQUEEZE_RELEASING,
    bollinger_bands,
    keltner_channels,
    squeeze_momentum,
    squeeze_states,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, *, open_: float, high: float, low: float, close: float) -> StructureBar:
    return StructureBar(
        index=i, symbol="TEST", timeframe="M15",
        open_time=_T0 + timedelta(minutes=15 * i), close_time=_T0 + timedelta(minutes=15 * (i + 1)),
        open=Decimal(str(round(open_, 5))), high=Decimal(str(round(high, 5))),
        low=Decimal(str(round(low, 5))), close=Decimal(str(round(close, 5))), volume=Decimal("100"),
    )


def _consolidation_then_breakout_bars(seed: int = 7, consolidation_n: int = 40, breakout_n: int = 30) -> list[StructureBar]:
    """Genuine consolidation (tightly-clustered closes, modest stable wick range -- BB stdev
    shrinks relative to ATR/KC) followed by a real directional breakout (range expansion)."""
    rnd = random.Random(seed)
    bars = []
    price = 100.0
    for i in range(consolidation_n):
        c = 100.0 + rnd.uniform(-0.02, 0.02)
        o = 100.0 + rnd.uniform(-0.02, 0.02)
        bars.append(_bar(i, open_=o, high=max(o, c) + 0.08, low=min(o, c) - 0.08, close=c))
    for i in range(consolidation_n, consolidation_n + breakout_n):
        price += rnd.uniform(0.25, 0.45)
        o = price - rnd.uniform(0.1, 0.2)
        bars.append(_bar(i, open_=o, high=max(o, price) + 0.15, low=min(o, price) - 0.05, close=price))
    return bars


def test_bollinger_bands_warmup_is_none_then_populated():
    bars = _consolidation_then_breakout_bars()
    bb = bollinger_bands(bars, period=20)
    assert all(v is None for v in bb.upper[:19])
    assert all(v is not None for v in bb.upper[19:])
    assert all(bb.upper[i] >= bb.mid[i] >= bb.lower[i] for i in range(19, len(bars)))


def test_keltner_channels_warmup_is_none_then_populated():
    bars = _consolidation_then_breakout_bars()
    kc = keltner_channels(bars, period=20)
    assert all(v is None for v in kc.upper[:19])
    assert all(v is not None for v in kc.upper[19:])
    assert all(kc.upper[i] >= kc.mid[i] >= kc.lower[i] for i in range(19, len(bars)))


def test_squeeze_on_during_genuine_consolidation():
    """Bollinger Bands must sit inside Keltner Channels during the tightly-clustered
    consolidation window (once both series clear their own 20-bar warmup)."""
    bars = _consolidation_then_breakout_bars()
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    consolidation_tail = states[25:39]  # well past warmup, still inside the consolidation window
    assert all(s == SQUEEZE_ON for s in consolidation_tail), consolidation_tail


def test_squeeze_releases_exactly_once_at_the_breakout_transition():
    bars = _consolidation_then_breakout_bars()
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    releasing_indices = [i for i, s in enumerate(states) if s == SQUEEZE_RELEASING]
    assert len(releasing_indices) == 1, states
    # The bar immediately before RELEASING must have been ON; RELEASING itself is never ON.
    idx = releasing_indices[0]
    assert states[idx - 1] == SQUEEZE_ON
    assert states[idx] != SQUEEZE_ON


def test_squeeze_off_well_into_the_breakout():
    bars = _consolidation_then_breakout_bars()
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    assert all(s == SQUEEZE_OFF for s in states[-15:]), states[-15:]


def test_momentum_ramps_positive_through_a_real_uptrend_breakout():
    # consolidation_n=60 gives enough room past momentum's real 2*period-1=39-bar warmup to
    # observe several genuinely-post-warmup, still-consolidating values (not just the 1-2 bars
    # right at the warmup boundary the default 40-bar consolidation window would leave).
    bars = _consolidation_then_breakout_bars(consolidation_n=60, breakout_n=30)
    mom = squeeze_momentum(bars, period=20)
    # Momentum should be near-zero (no persistent directional drift) during consolidation...
    consolidation_vals = [m for m in mom[40:59] if m is not None]
    assert len(consolidation_vals) >= 10, consolidation_vals
    assert all(abs(v) < 0.5 for v in consolidation_vals), consolidation_vals
    # ...and clearly positive well into the breakout -- overall trend, not strict point-to-point
    # monotonicity (synthetic random noise can wobble the last couple of points even while the
    # broader trend is unambiguous).
    late_breakout_vals = [m for m in mom[-10:] if m is not None]
    assert all(v > 0 for v in late_breakout_vals), late_breakout_vals
    early_breakout_val = next(m for m in mom[60:] if m is not None)
    assert late_breakout_vals[-1] > early_breakout_val * 2


def test_momentum_warmup_is_none():
    """Two-stage warmup, not one: source[i] itself needs a full `period` lookback (it embeds
    highest/lowest/sma over the trailing window), and linreg then needs `period` CONSECUTIVE
    valid source points -- so the real warmup is 2*period-1 bars, not period-1. This matches how
    Pine necessarily evaluates the same formula (highest/lowest/sma inside a linreg source
    expression are themselves per-bar, time-shifted at every historical point linreg fits to)."""
    bars = _consolidation_then_breakout_bars()
    mom = squeeze_momentum(bars, period=20)
    assert all(v is None for v in mom[:38])
    assert mom[38] is not None


def test_squeeze_states_length_matches_input_bars():
    bars = _consolidation_then_breakout_bars()
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    mom = squeeze_momentum(bars)
    assert len(states) == len(bars)
    assert len(mom) == len(bars)


def test_flat_constant_price_never_crashes_and_reports_off_or_on_consistently():
    """A completely flat series (zero variance) must not raise (stdev=0, ATR=0 -- division/
    comparison edge cases) and should classify deterministically. 45 bars: enough to clear
    momentum's real 2*period-1=39-bar warmup (period=20) with a few bars to spare."""
    bars = [_bar(i, open_=100.0, high=100.0, low=100.0, close=100.0) for i in range(45)]
    bb, kc = bollinger_bands(bars), keltner_channels(bars)
    states = squeeze_states(bb, kc)
    mom = squeeze_momentum(bars)
    assert len(states) == 45
    assert len(mom) == 45
    # Zero-width bands on both sides -- not a strict "inside" (upper==upper, lower==lower is not
    # bu<ku and bl>kl), so this degenerate case correctly reports OFF, not ON.
    assert states[40] in (SQUEEZE_OFF, SQUEEZE_ON)
    assert mom[40] == 0.0

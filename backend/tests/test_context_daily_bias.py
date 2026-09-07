"""BSI Daily Bias Audit implementation: tests for StrategyContext.htf_trend_daily / daily_snapshot
and build_strategy_context()'s new, purely-additive `daily_rows`/`daily_snapshot` parameters.

Covers: backward compatibility (every existing caller not supplying daily_rows sees identical
behavior to before this change), correct trend derivation when daily_rows IS supplied, graceful
insufficient-data handling, and that ctx.htf_trend_h4/h4_rows are completely untouched by this
addition (the field every other, non-BSI consumer -- fusion.py, smc_continuation.py, etc. -- still
depends on).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.engine import analyze_bars
from backend.mt5_strategies.context import build_strategy_context

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def _row(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": (NOW + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": l, "close": c, "tick_volume": 500, "spread": 2}


def _flat_rows(n: int, price: float = 1.1000) -> list[dict]:
    return [_row(i, price, price + 0.0003, price - 0.0003, price) for i in range(n)]


def _zigzag_daily_rows(*, legs: list[float], n_per_leg: int = 4, start_price: float = 1.1000) -> list[dict]:
    """Alternating up/down legs (matching test_bsi_engine.py's own established _leg_rows()
    pattern) -- a real market never moves in a single monotonic straight line, and classify_trend()
    needs genuine confirmed swing highs/lows (local pivots with bars on both sides) to produce a
    directional read at all; a laser-straight synthetic trend produces zero real swings beyond the
    very first bar and stays "unknown" forever, exactly the failure mode this helper avoids."""
    rows = []
    price = start_price
    day = 0
    for leg_size in legs:
        step = leg_size / n_per_leg
        for _ in range(n_per_leg):
            o = price
            price += step
            c = price
            hi = max(o, c) + abs(step) * 0.15
            lo = min(o, c) - abs(step) * 0.15
            rows.append({"symbol": "EURUSD", "timeframe": "D1", "time": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat(), "open": o, "high": hi, "low": lo, "close": c, "tick_volume": 5000})
            day += 1
    return rows


def _uptrend_daily_rows() -> list[dict]:
    # Net-bullish zigzag: bigger up-legs than down-legs, several alternations -- produces genuine
    # higher-highs/higher-lows confirmed swings, not just one monotonic push.
    return _zigzag_daily_rows(legs=[0.0300, -0.0100, 0.0280, -0.0090, 0.0260, -0.0080, 0.0240], n_per_leg=4)


def _downtrend_daily_rows() -> list[dict]:
    return _zigzag_daily_rows(legs=[-0.0300, 0.0100, -0.0280, 0.0090, -0.0260, 0.0080, -0.0240], n_per_leg=4)


def _base_kwargs():
    m15 = _flat_rows(60)
    return dict(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=m15, h1_rows=m15, h4_rows=m15, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"))


def test_htf_trend_daily_is_none_when_daily_rows_omitted_backward_compatible():
    """Every existing caller that doesn't yet pass daily_rows (the default for all of them until
    this task's own call-site updates) must see IDENTICAL behavior to before this field existed."""
    ctx = build_strategy_context(**_base_kwargs())
    assert ctx is not None
    assert ctx.htf_trend_daily is None
    assert ctx.daily_snapshot is None


def test_existing_h4_fields_completely_unaffected_by_daily_rows_addition():
    """The single most important regression guard: ctx.htf_trend_h4/ctx.h4_rows/ctx.h4_snapshot
    must be byte-identical whether or not daily_rows is supplied -- fusion.py, smc_continuation.py,
    mean_reversion.py, trend_pullback.py all still depend on htf_trend_h4 for unrelated purposes
    this task does not touch."""
    kwargs = _base_kwargs()
    ctx_without_daily = build_strategy_context(**kwargs)
    ctx_with_daily = build_strategy_context(**kwargs, daily_rows=_uptrend_daily_rows())
    assert ctx_without_daily.htf_trend_h4 == ctx_with_daily.htf_trend_h4
    assert ctx_without_daily.h4_rows == ctx_with_daily.h4_rows
    assert (ctx_without_daily.h4_snapshot is None) == (ctx_with_daily.h4_snapshot is None)


def test_htf_trend_daily_detects_bullish_from_real_daily_bars():
    ctx = build_strategy_context(**_base_kwargs(), daily_rows=_uptrend_daily_rows())
    assert ctx is not None
    assert ctx.htf_trend_daily == "bullish"
    assert ctx.daily_snapshot is not None


def test_htf_trend_daily_detects_bearish_from_real_daily_bars():
    ctx = build_strategy_context(**_base_kwargs(), daily_rows=_downtrend_daily_rows())
    assert ctx is not None
    assert ctx.htf_trend_daily == "bearish"


def test_htf_trend_daily_stays_none_with_insufficient_daily_bars():
    """Fewer than the minimum (10) daily bars must degrade gracefully to None, never raise and
    never fabricate a trend from too little data."""
    ctx = build_strategy_context(**_base_kwargs(), daily_rows=_uptrend_daily_rows()[:5])
    assert ctx is not None
    assert ctx.htf_trend_daily is None
    assert ctx.daily_snapshot is None


def test_daily_snapshot_precomputed_bypasses_daily_rows_recomputation():
    """Mirrors the existing h4_snapshot/h1_snapshot precomputed-cache contract exactly (Part 5's
    Redis deterministic-computation cache) -- passing daily_snapshot directly must be accepted
    without needing daily_rows at all."""
    daily_rows = _uptrend_daily_rows()
    precomputed = analyze_bars(daily_rows, symbol="EURUSD", timeframe="D1")
    ctx = build_strategy_context(**_base_kwargs(), daily_snapshot=precomputed)
    assert ctx is not None
    assert ctx.htf_trend_daily == "bullish"
    assert ctx.daily_snapshot is precomputed

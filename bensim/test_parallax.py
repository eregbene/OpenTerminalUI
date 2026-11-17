"""Hand-made prices -> the ParallaxFX rules do exactly what the posts say.  python -m pytest -q bensim/test_parallax.py"""
import numpy as np
import pandas as pd
import pytest

from bensim.parallax import Path, legs_for, ny_bars, setups, simulate


def test_daily_bars_close_at_new_york_17h_both_seasons():
    t = np.arange(pd.Timestamp("2026-03-05", tz="UTC").timestamp(), pd.Timestamp("2026-03-12", tz="UTC").timestamp(), 300).astype(np.int64)
    one = np.ones(len(t))
    b = ny_bars({"t": t, "open": one, "high": one, "low": one, "close": one}, 24)
    hours = pd.to_datetime(b.t_open[1:], unit="s").dt.hour.unique()  # US DST starts Sun 8 Mar 2026
    assert set(hours) == {22, 21}  # 17:00 EST = 22:00 UTC, 17:00 EDT = 21:00 UTC


def test_pattern_indecision_then_bullish_engulfing():
    bars = pd.DataFrame({"open": [1.50, 1.48], "high": [1.55, 1.70], "low": [1.45, 1.40], "close": [1.51, 1.66]})
    s = setups(bars, location=False)
    assert list(s.index) == [1] and int(s.dir.iloc[0]) == 1
    bars.loc[1, "close"] = 1.505  # no higher close than the indecision bar -> no setup
    assert setups(bars, location=False).empty


def test_fib_legs_on_the_setup_candle():
    c = lambda close: pd.Series({"dir": 1, "high": 2.0, "low": 1.0, "close": close})  # noqa: E731
    assert legs_for(c(1.9), "author") == [(pytest.approx(1.764), pytest.approx(1.214), "limit"), (pytest.approx(1.618), pytest.approx(1.214), "limit")]
    assert len(legs_for(c(1.7), "author")) == 1  # closed past 23.6% -> only the 38.2% order
    assert legs_for(c(1.5), "author") == []  # closed past 38.2% -> no trade
    assert legs_for(c(1.9), "stop_entry") == [(2.0, 1.0, "stop")]


def _path(highs, lows, spr=0.0):
    n = len(highs)
    h, lo = np.array(highs, float), np.array(lows, float)
    return Path(np.arange(n, dtype=np.int64) * 300, h.copy(), h, lo, (h + lo) / 2, np.full(n, spr), np.zeros(n))  # open = high: no gaps


def test_limit_fill_then_target():
    p = _path([1.90, 1.80, 2.20, 2.70], [1.80, 1.70, 1.90, 2.30])
    p.o[:] = [1.85, 1.78, 1.95, 2.35]  # opens inside the previous range: no gaps
    r, _, _ = simulate(p, 0, 1, 1.764, 1.214, 2.618, "limit", 10**6, 10**6)
    assert r == pytest.approx((2.618 - 1.764) / (1.764 - 1.214))


def test_bar_touching_stop_and_target_is_a_loss():
    p = _path([1.90, 1.80, 2.70], [1.80, 1.70, 1.10])
    r, _, _ = simulate(p, 0, 1, 1.764, 1.214, 2.618, "limit", 10**6, 10**6)
    assert r == pytest.approx(-1.0)


def test_target_reached_before_fill_cancels_the_order():
    p = _path([2.70, 1.80], [1.90, 1.70])
    assert simulate(p, 0, 1, 1.764, 1.214, 2.618, "limit", 10**6, 10**6) is None


def test_buy_limit_needs_the_ask_to_reach_the_entry():
    p = _path([1.90, 1.80], [1.80, 1.762], spr=0.01)  # bid 1.762 -> ask 1.772 > 1.764: not filled
    assert simulate(p, 0, 1, 1.764, 1.214, 2.618, "limit", 10**6, 10**6) is None


def test_gap_below_a_buy_limit_fills_at_the_better_open():
    p = _path([1.90, 1.75], [1.80, 1.70])
    p.o[1] = 1.75  # opens below the 1.764 limit
    r, _, _ = simulate(p, 0, 1, 1.764, 1.214, 2.618, "limit", 10**6, 10**6)  # held to the end: time exit at close 1.725
    assert r == pytest.approx((1.725 - 1.75) / (1.75 - 1.214))


def test_sell_limit_fills_on_bid_and_exits_on_ask():
    p = _path([1.45, 1.52, 1.40], [1.40, 1.45, 0.985], spr=0.01)
    p.o[:] = [1.42, 1.46, 1.44]
    r, _, _ = simulate(p, 0, -1, 1.5, 1.8, 1.0, "limit", 10**6, 10**6)  # ask low 0.995 <= 1.0 -> target
    assert r == pytest.approx((1.5 - 1.0) / (1.8 - 1.5))
    p.lo[2] = 0.995  # ask low 1.005 never reaches the 1.0 target -> held to the end (ask close)
    r2, _, _ = simulate(p, 0, -1, 1.5, 1.8, 1.0, "limit", 10**6, 10**6)
    assert r2 < r


def test_open_sky_blocks_a_buy_under_a_recent_swing_high():
    from bensim.parallax import _open_sky
    hi = [1.0, 1.1, 1.5, 1.1, 1.0, 1.0, 1.2]  # fractal high 1.5 at bar 2, confirmed from bar 4
    b = pd.DataFrame({"high": hi, "low": [h - 0.4 for h in hi]})
    ok = _open_sky(b, 1)
    assert not ok.iloc[6]  # bar 6: high 1.2, -100 ext 1.6 -> the 1.5 swing high is in the way
    b.loc[6, ["high", "low"]] = [1.6, 1.55]  # already above it
    assert _open_sky(b, 1).iloc[6]

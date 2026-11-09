"""Hand-made daily bars -> the CTA trend-momentum entry, stop and trailing do what the post says."""
import pandas as pd

from bensim.cta_momentum import trade


def _bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_buy_stop_fill_trail_under_completed_pullback_then_stopped():
    b = _bars([
        (1.00, 1.10, 1.00, 1.09),  # 0 momentum bar: entry 1.10+buffer, stop at mid 1.05
        (1.09, 1.12, 1.08, 1.11),  # 1 fills at 1.101
        (1.11, 1.20, 1.10, 1.19),  # 2 new high 1.20
        (1.19, 1.18, 1.14, 1.15),  # 3 pullback bar 1
        (1.15, 1.17, 1.13, 1.16),  # 4 pullback bar 2 (low 1.13)
        (1.16, 1.25, 1.15, 1.24),  # 5 closes above 1.20 -> stop to 1.13
        (1.24, 1.24, 1.12, 1.12),  # 6 hits the trailed stop 1.13
    ])
    fill, px, k, j = trade(b, 0, 1, 0.001)
    assert (round(fill, 3), px, k, j) == (1.101, 1.13, 1, 6)


def test_midpoint_traded_first_cancels():
    b = _bars([(1.00, 1.10, 1.00, 1.09), (1.09, 1.09, 1.04, 1.06), (1.06, 1.12, 1.06, 1.11)])  # bar 1: midpoint, no trigger
    assert trade(b, 0, 1, 0.001) is None


def test_fill_day_reaching_the_stop_is_a_loss():
    b = _bars([(1.00, 1.10, 1.00, 1.09), (1.09, 1.12, 1.04, 1.05)])
    assert trade(b, 0, 1, 0.001)[1] == 1.05


def test_swing_structure_needs_two_rising_fractal_highs():
    import numpy as np

    from bensim.cta_momentum import _swings_rising
    x = np.array([1, 2, 5, 2, 1, 2, 7, 2, 1, 1.0])  # fractal highs 5 (bar 2) then 7 (bar 6), the 2nd known at bar 8
    r = _swings_rising(x, 1)
    assert not r[7] and r[8]

"""Flow ML ATR-expansion filter: ratio of mean H1 range, last 6 / last 48 closed bars."""
import numpy as np

from bensim import flow_live as F


def test_atr_ratio():
    low = np.zeros(48)
    assert F.atr_ratio(np.ones(48), low) == 1.0  # flat volatility
    hi = np.ones(48)
    hi[-6:] = 3.0  # last 6 bars 3x bigger: 3 / mean(42*1 + 6*3)/48 = 3 / 1.25
    assert abs(F.atr_ratio(hi, low) - 2.4) < 1e-9
    assert np.isnan(F.atr_ratio(np.ones(47), np.zeros(47)))  # not enough bars -> NaN -> engine skips


def test_default_min_is_1_2():
    assert F.ML_ATR_MIN == 1.2

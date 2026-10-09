"""Bensim research engine guards: no look-ahead, HTF sync, conservative intrabar fills.

Run: python -m pytest bensim_research/test_bensim.py -q   (needs the cached/DB EURUSD M15 data)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bensim_research import backtest as B
from bensim_research import data as D
from bensim_research import features as F
from bensim_research import strategy as S


def _bars(n: int = 18000) -> D.Bars:
    full, _ = D.load("EURUSD", "M15")
    return full.slice(len(full) - n, len(full))


def test_htf_bar_invisible_until_closed():
    b = _bars(2000)
    h4 = D.resample(b, "H4")
    i = 10
    opened = h4.t[i]
    assert D.last_closed(h4, np.array([opened + 14400 - 900]))[0] == i - 1  # 15m before close: still forming
    assert D.last_closed(h4, np.array([opened + 14400]))[0] == i


def test_features_unchanged_by_future_bars():
    full = D.resample(_bars(), "H1")
    cut = len(full) - 400
    part = full.slice(0, cut)
    for k in (2, 3):
        a, p = F.structure(full, k), F.structure(part, k)
        for name in ("trend", "brk", "sh", "sl"):
            np.testing.assert_array_equal(getattr(a, name)[:cut], getattr(p, name), err_msg=f"{name} k={k}")
    ra = F.regime(full, F.atr(full), F.structure(full, 3)).iloc[:cut].reset_index(drop=True)
    rp = F.regime(part, F.atr(part), F.structure(part, 3))
    pd.testing.assert_frame_equal(ra, rp)


def test_candidates_unchanged_by_future_bars():
    base = _bars()
    cut_t = int(base.t[-3000])
    st = S.STACKS["H"]
    full = S.build("EURUSD", st, base_bars=base).cands
    part = S.build("EURUSD", st, base_bars=base.slice(0, int(np.searchsorted(base.t, cut_t)))).cands
    cols = ["j", "dir", "top", "bottom", "sweep_tier", "origin", "brk", "bias_trend", "loc_trend", "regime",
            "phase", "sd", "loc_fvg", "pd", "liq1", "score"]
    known = full[full.t_known <= cut_t - 86400 * 8][cols].reset_index(drop=True)  # past W1 bucket edge
    other = part[part.t_known <= cut_t - 86400 * 8][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(known, other)


def test_same_bar_stop_and_target_is_a_loss():
    t = np.arange(10, dtype=np.int64) * 900
    px = np.full(10, 1.1000)
    b = D.Bars("M15", t, px.copy(), px + 0.0005, px - 0.0005, px.copy(), np.ones(10))
    b.h[3], b.l[3] = 1.1100, 1.0900  # one bar spans both stop and target
    st = F.structure(b, 2)
    ctx = S.Context("EURUSD", S.STACKS["H"], b, b, np.full(10, 0.001), st, np.zeros(10), np.zeros(10), np.zeros(10, np.int8), pd.DataFrame())
    res = B._manage(ctx, 1, 1, 1.1000, 1.0950, 1.1050, np.nan, 0.0, False, "none", True, 0.0050)
    assert res["why"] == "stop" and abs(res["gross_r"] + 1.0) < 1e-9

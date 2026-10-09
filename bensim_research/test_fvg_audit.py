"""FVG audit unit tests: detector definition/indexing, lifecycle, touches, fills, mirroring, sync.

Run: python -m pytest bensim_research/test_fvg_audit.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bensim_research import data as D
from bensim_research import features as F
from bensim_research import fvg_audit as A


def mk(rows: list[tuple[float, float, float, float]], tf: str = "M15", t0: int = 1_700_000_100 // 900 * 900) -> D.Bars:
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    t = t0 + np.arange(len(rows), dtype=np.int64) * D.TF_SECONDS[tf]
    return D.Bars(tf, t, o, h, l, c, np.ones(len(rows)))


UP = [(1.00, 1.01, 0.99, 1.005), (1.005, 1.05, 1.004, 1.045), (1.045, 1.06, 1.02, 1.055)]  # c1 high 1.01 < c3 low 1.02


def test_bullish_definition_edges_and_indexing():
    b = mk(UP)
    f = F.fvgs(b, np.full(3, 0.01))
    assert len(f) == 1
    r = f.iloc[0]
    assert (r.dir, r.j, r.top, r.bottom) == (1, 2, 1.02, 1.01)
    assert r.t_known == b.t[2] + 900  # known only when candle 3 closes
    assert abs(r.disp_atr - 4.0) < 1e-9  # middle body 0.04 / ATR 0.01


def test_bearish_is_mirror_and_touching_wicks_are_not_a_gap():
    dn = [(2 - o, 2 - l, 2 - h, 2 - c) for o, h, l, c in UP]
    f = F.fvgs(mk(dn), np.full(3, 0.01))
    assert len(f) == 1 and f.iloc[0].dir == -1
    assert np.isclose(f.iloc[0].top, 2 - 1.01) and np.isclose(f.iloc[0].bottom, 2 - 1.02)
    flat = UP[:2] + [(1.045, 1.06, 1.01, 1.055)]  # c3 low == c1 high -> no imbalance
    assert len(F.fvgs(mk(flat), np.full(3, 0.01))) == 0


def test_never_both_directions_on_one_bar_and_chained_flag():
    b = D.resample(D.load("EURUSD", "M15")[0].slice(0, 20000), "H1")
    f = F.fvgs(b, F.atr(b))
    assert not f.duplicated(["j"]).any()  # bull needs h1<l3, bear needs l1>h3: mutually exclusive
    steps = [(1 + i * 0.01, 1.008 + i * 0.01, 0.999 + i * 0.01, 1.007 + i * 0.01) for i in range(6)]
    g = F.fvgs(mk(steps), np.full(6, 0.01))
    assert list(g.j) == [2, 3, 4, 5]  # a 6-candle run gives 4 FVGs; a trader sees one zone
    assert list(A.chained(g.j.to_numpy(), g.dir.to_numpy())) == [False, True, True, True]
    assert not A.chained(np.array([5, 6]), np.array([1, -1])).any()  # opposite directions are not a chain


def test_lifecycle_touch_levels_full_and_close_beyond():
    T, B = 1.02, 1.01  # gap 100 pips; lows walk down into it
    L = np.array([1.03, 1.0205, 1.0199, 1.0174, 1.0150, 1.0125, 1.0099, 1.0120])
    C = np.array([1.035, 1.03, 1.025, 1.022, 1.021, 1.015, 1.0095, 1.013])
    lc = A.lifecycle(L, C, T, B)
    assert (lc["touch_i"], lc["q25_i"], lc["q50_i"], lc["q75_i"], lc["full_i"], lc["beyond_i"]) == (2, 3, 4, 5, 6, 6)
    assert np.isclose(lc["max_pen"], (T - 1.0099) / (T - B))
    assert A.lifecycle(np.array([1.03, 1.025]), np.array([1.03, 1.03]), T, B)["touch_i"] == A.NA


def test_distinct_touches_need_price_to_leave_the_gap():
    L = np.array([1.03, 1.019, 1.018, 1.025, 1.015, 1.016, 1.021, 1.02])
    assert list(A.touch_starts(L, 1.02)) == [1, 4, 7]


def _path(h, l, c=None, step=900):
    t = np.arange(len(h), dtype=np.int64) * step
    return t, np.array(h, float), np.array(l, float), np.array(c if c is not None else l, float)


def test_limit_fill_bar_counts_stop_but_not_target():
    t, h, l, c = _path([1.03, 1.05], [0.98, 1.0])  # fill bar spans -1R and +2R; next bar irrelevant
    r = A.excursion(t, h, l, c, 0, 1.0, 0.01, 900, limit=True)
    assert r["w1"] == 0.0 and np.isclose(r["mfe"], 5.0)  # fill-bar high ignored, later high still counts as MFE
    t, h, l, c = _path([1.005, 1.015], [0.995, 1.001])  # fill bar harmless, next bar hits +1R
    assert A.excursion(t, h, l, c, 0, 1.0, 0.01, 900, limit=True)["w1"] == 1.0


def test_same_bar_stop_and_target_scores_half_and_unresolved_is_nan():
    t, h, l, c = _path([1.0, 1.02], [1.0, 0.98])
    r = A.excursion(t, h, l, c, 0, 1.0, 0.01, 900, limit=False)
    assert r["w1"] == 0.5 and r["amb"] == 1.0 and r["w2"] == 0.5
    t, h, l, c = _path([1.001] * 5, [0.999] * 5)
    assert np.isnan(A.excursion(t, h, l, c, 0, 1.0, 0.01, 900, limit=False)["w1"])


def test_returns_use_close_n_bars_later():
    t, h, l, c = _path([1.0] * 30, [1.0] * 30, c=np.linspace(1.0, 1.029, 30))
    r = A.excursion(t, h, l, c, 0, 1.0, 0.01, 900, limit=False)
    assert np.isclose(r["r5"], (c[4] - 1.0) / 0.01) and np.isclose(r["r20"], (c[19] - 1.0) / 0.01)
    assert np.isnan(r["r50"])  # beyond the data -> no fake value


def test_bearish_outcomes_equal_mirrored_bullish():
    rng = np.random.default_rng(0)
    c = 1.1 + np.cumsum(rng.normal(0, 0.0004, 3000))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.0002
    l = np.minimum(o, c) - 0.0002
    t = 1_700_000_100 // 900 * 900 + np.arange(3000, dtype=np.int64) * 900
    up = D.Bars("M15", t, o, h, l, c, np.ones(3000))
    down = D.Bars("M15", t, 3 - o, 3 - l, 3 - h, 3 - c, np.ones(3000))
    fu, fd = F.fvgs(up, F.atr(up)), F.fvgs(down, F.atr(down))
    fu, fd = fu[fu.j >= 50].reset_index(drop=True), fd[fd.j >= 50].reset_index(drop=True)
    ou = A.outcomes(fu, up, up, np.random.default_rng(1))
    od = A.outcomes(fd, down, down, np.random.default_rng(1))
    cols = [x for x in ou.columns if x.startswith(("A_", "C_", "E_", "touch", "full", "max_pen", "n_touches"))]
    pd.testing.assert_frame_equal(ou[cols], od[cols], check_dtype=False, atol=1e-9)
    assert ((ou.touch == 1) == (ou.n_touches > 0)).all()  # lifecycle and touch count share one window
    assert (ou.touch_bars.dropna() >= 1).all()  # a touch can only happen after candle 3


def test_sessions_and_day_week_buckets_are_utc():
    ts = np.array([6 * 3600 + 3540, 7 * 3600, 12 * 3600, 16 * 3600, 21 * 3600]) + 1_699_833_600  # a Monday 00:00 UTC
    assert list(D.session_of(ts)) == ["ASIA", "LONDON", "OVERLAP", "NEWYORK", "OFF"]
    b = mk([(1, 1, 1, 1)] * (4 * 24 * 3), t0=1_699_833_600 - 4 * 3600)  # Sunday 20:00 UTC, 3 days of M15
    d1 = D.resample(b, "D1")
    assert all((d1.t + 7200) % 86400 == 0)  # FX day rolls at 22:00 UTC
    assert len(d1) == 3 and pd.Timestamp(d1.t[0], unit="s").day_name() == "Sunday"  # Sun 20:00-22:00 joins Monday: no stub day
    assert d1.known_at[0] == 1_699_833_600 + 79200  # Monday's day closes Monday 22:00
    w1 = D.resample(b, "W1")
    assert len(w1) == 1  # the Sunday open belongs to the new week


def test_audit_features_unchanged_by_future_bars():
    base = D.load("EURUSD", "M15")[0]
    base = base.slice(len(base) - 30000, len(base))
    cut = len(base) - 3000

    def feats(bb):
        tfs = {tf: bb if tf == "M15" else D.resample(bb, tf) for tf in ("M15", "H1", "H4", "D1", "W1")}
        return A.features("EURUSD", "H1", tfs, bb)

    full, part = feats(base), feats(base.slice(0, cut))
    lim = base.t[cut] - 8 * 86400  # past any open W1/D1 bucket at the cut
    cols = ["j", "dir", "top", "bottom", "htf_state", "sweep_tier", "struct", "in_demand", "in_supply",
            "near_pd", "near_pw", "near_eq", "pd", "dist_target_liq", "chained", "session", "disp_class"]
    a = full[full.t_known <= lim][cols].reset_index(drop=True)
    p = part[part.t_known <= lim][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, p)

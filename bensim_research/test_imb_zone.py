"""The imbalance-video strategy must reproduce the trades shown in the video (D:/Downloads/imbalance.mp4).

Run: python -m pytest bensim_research/test_imb_zone.py -q   (needs the DB / cached FOREXSB bars)
"""
from __future__ import annotations

import pandas as pd

from bensim_research import data as D
from bensim_research import imb_zone as I


def _trades(sym: str, tf: str) -> pd.DataFrame:
    base = I.load_base(sym)
    return I.trades(sym, tf, D.resample(base, tf), base, I.VARIANTS["video rules"])[0]


def _on(t: pd.DataFrame, day: str) -> pd.DataFrame:
    return t[pd.to_datetime(t.t_entry, unit="s").dt.strftime("%Y-%m-%d") == day]


def test_eurusd_30m_video_trade():  # video 7:01-7:22: buy the 30m demand zone, stop below it
    t = _on(_trades("EURUSD", "M30"), "2024-04-19")
    assert len(t) == 1 and t.iloc[0].dir == 1
    assert abs(t.iloc[0].entry - 1.06193) < 0.0001 and abs(t.iloc[0].stop - 1.06047) < 0.0001


def test_audusd_daily_video_trade():  # video 4:43-4:55: buy once the extreme imbalance is filled
    t = _on(_trades("AUDUSD", "D1"), "2024-04-16")
    assert len(t) == 1 and t.iloc[0].dir == 1 and 0.635 < t.iloc[0].entry < 0.640


def test_data_holes_ignore_weekend_but_catch_missing_days():
    import numpy as np
    mon = 1_699_833_600  # Monday 00:00 UTC
    t = np.r_[np.arange(mon, mon + 4 * 86400 + 21 * 3600, 900),  # Mon..Fri 21:00
              np.arange(mon + 6 * 86400 + 21 * 3600, mon + 8 * 86400, 900),  # Sun 21:00 .. Tue 00:00 (normal weekend)
              np.arange(mon + 9 * 86400, mon + 10 * 86400, 900)]  # Tue missing entirely
    one = np.ones(len(t))
    spans = D.holes(D.Bars("M15", t.astype(np.int64), one, one, one, one, one))
    assert len(spans) == 1 and spans[0][0] == mon + 8 * 86400 and spans[0][1] == mon + 9 * 86400
    hit = D.overlaps_hole(spans, np.array([mon + 7 * 86400, mon + 8 * 86400 - 900]), np.array([mon + 7 * 86400 + 3600, mon + 9 * 86400 + 900]))
    assert list(hit) == [False, True]

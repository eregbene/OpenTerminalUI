"""Bennett CHANNEL mode -- his range trade ("How I Made $13,491 With One Simple SMC Trade", USDCAD 9-13 Mar 2026).
Rules FROZEN before any test (buy; sell = mirror at the channel top):
  1 context   4H range inside a parallel channel: least-squares line through the 4H mid prices of the last N bars
              (closed bars only); channel top/bottom = line + the highest high / lowest low residual. A RANGE needs the
              line's total drift over N bars < 50% of the channel width ("higher highs and higher lows, but mostly
              sideways").
  2 location  a 15M low inside the bottom quarter of the channel ("we're right there in that area of interest").
  3 trigger   15M internal CHoCH: after that low, a 15M CLOSE above the last 15M swing high (3-candle) made before it.
  4 entry     "in a sideways market there is a good chance we pull back after the CHoCH": limit at the 62-79% OTE of
              the CHoCH leg (low -> highest high since); filled within 24 h, cancelled if price closes below the low.
  5 stop      the leg low - 3.5 pips ("3 or 4 pips below that low").
  6 target    the channel's top quarter (the buy-side imbalances near the top); skip if < 3R ("3R minimum");
              Friday 20:00 UTC close ("pay myself before the weekend"), 10-day max.
    python -m bensim.bennett_channel --start 2026-08-07 --end 2026-11-07
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .backtest import load_m5, resample
from .strategy import pip

N4 = 90  # 4H bars for the channel (~15 trading days)
COST = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}


def channel(h4: dict, j: int) -> tuple[float, float, float] | None:
    """(slope per bar, bottom at bar j, top at bar j) from the N4 closed 4H bars before j, or None if not a range."""
    if j < N4:
        return None
    hi, lo = h4["high"][j - N4:j], h4["low"][j - N4:j]
    x = np.arange(N4)
    k, c = np.polyfit(x, (hi + lo) / 2, 1)
    line = k * x + c
    up, dn = float((hi - line).max()), float((lo - line).min())
    width = up - dn
    if width <= 0 or abs(k * N4) >= 0.5 * width:
        return None  # trending, not a range
    at = k * N4 + c
    return k, at + dn, at + up


def setup(h4: dict, m15: dict, i: int, d: int, p: float) -> dict | None:
    """Rules 1-3 + the order (4-6) for the 15M bar i that just closed: {entry, stop, ext, target} or None.
    h4 must hold only 4H bars closed by the close of bar i. Shared by the backtest (run) and the live book."""
    h, lo, c = m15["high"], m15["low"], m15["close"]
    if i < 41:
        return None
    ch = channel(h4, len(h4["t"]))
    if ch is None:
        return None
    _, bot, top = ch
    w = top - bot
    # 2 location: the extreme of the last 32 15M bars is inside the outer quarter
    seg = slice(max(0, i - 32), i + 1)
    ext_i = seg.start + int(np.argmin(lo[seg]) if d == 1 else np.argmax(h[seg]))
    ext = float(lo[ext_i] if d == 1 else h[ext_i])
    if (ext > bot + 0.25 * w) if d == 1 else (ext < top - 0.25 * w):
        return None
    # 3 trigger: this close breaks the last 3-candle swing (high for a buy) made before the extreme
    sw = [k for k in range(max(1, ext_i - 40), ext_i) if ((h[k] > h[k - 1] and h[k] > h[k + 1]) if d == 1 else (lo[k] < lo[k - 1] and lo[k] < lo[k + 1]))]
    if not sw:
        return None
    lvl = h[sw[-1]] if d == 1 else lo[sw[-1]]
    if not ((c[i] > lvl >= c[i - 1]) if d == 1 else (c[i] < lvl <= c[i - 1])):
        return None
    leg_hi = float(h[ext_i:i + 1].max() if d == 1 else lo[ext_i:i + 1].min())
    entry = leg_hi - 0.62 * (leg_hi - ext)  # first touch of the OTE band (62% of the CHoCH leg), both sides
    stop = ext - d * 3.5 * p
    target = (top - 0.25 * w) if d == 1 else (bot + 0.25 * w)
    risk = (entry - stop) * d
    if risk <= 0 or (target - entry) * d < 3 * risk:
        return None
    return {"entry": float(entry), "stop": float(stop), "ext": ext, "target": float(target)}


def h4_closed(h4: dict, t_close: int) -> dict:
    """The 4H bars closed by t_close."""
    j = int(np.searchsorted(h4["t"] + 14400, t_close, side="right"))
    return {k: v[:j] for k, v in h4.items()}


def run(sym: str, start: str, end: str, sides: tuple = (1, -1)) -> list[dict]:
    m5 = load_m5(sym, (pd.Timestamp(start) - pd.Timedelta(days=40)).strftime("%Y-%m-%d"), end)
    if len(m5["t"]) < 5000:
        return []
    h4, m15 = resample(m5, 14400), resample(m5, 900)
    p, t0 = pip(sym), pd.Timestamp(start, tz="UTC").timestamp()
    t = m15["t"]
    out, busy = [], 0
    for i in range(10, len(t)):
        if t[i] < t0 or t[i] < busy:
            continue
        hb = h4_closed(h4, int(t[i]) + 900)
        for d in sides:
            s = setup(hb, m15, i, d, p)
            if s is None:
                continue
            tr = _fill_and_exit(m5, sym, d, int(t[i]) + 900, s["entry"], s["stop"], s["ext"], s["target"], p)
            if tr:
                out.append(tr)
                busy = tr["t_exit"]
                break
    return out


def _fill_and_exit(m5, sym, d, t_from, entry, stop, ext, target, p) -> dict | None:
    t, h, lo, c = m5["t"], m5["high"], m5["low"], m5["close"]
    i0 = int(np.searchsorted(t, t_from))
    i1 = int(np.searchsorted(t, t_from + 86400))
    k = None
    for i in range(i0, min(i1, len(t))):
        if (c[i] < ext) if d == 1 else (c[i] > ext):
            return None  # the low broke before the pullback filled
        if (lo[i] <= entry) if d == 1 else (h[i] >= entry):
            k = i
            break
    if k is None:
        return None
    risk = (entry - stop) * d
    end = min(len(t), int(np.searchsorted(t, t[k] + 10 * 86400)))
    spr = 0.8 * p if sym != "XAUUSD" else 2.5 * p
    for i in range(k, end):
        ts = pd.Timestamp(int(t[i]), unit="s")
        if ts.dayofweek == 4 and ts.hour >= 20:
            x = c[i] + (spr if d == -1 else 0)
            return _row(sym, d, t[k], t[i], entry, stop, target, (x - entry) * d / risk, "friday", p, risk)
        hh, ll = h[i] + (spr if d == -1 else 0), lo[i] + (spr if d == -1 else 0)
        if (ll <= stop) if d == 1 else (hh >= stop):
            return _row(sym, d, t[k], t[i], entry, stop, target, -1.0, "stop", p, risk)
        if (hh >= target) if d == 1 else (ll <= target):
            return _row(sym, d, t[k], t[i], entry, stop, target, (target - entry) * d / risk, "target", p, risk)
    return None


def _row(sym, d, tk, ti, entry, stop, target, r_gross, why, p, risk) -> dict:
    return {"sym": sym, "dir": d, "t_entry": int(tk), "t_exit": int(ti), "entry": entry, "sl": stop, "tp": target,
            "r": r_gross - COST[sym] * p / risk, "why": why, "risk_pips": risk / p}


def _selfcheck() -> None:
    x = np.arange(200, dtype=float)
    mid = 1.0 + 0.002 * np.sin(x / 8)  # a flat, oscillating market = a range
    h4 = {"t": x * 14400, "high": mid + 0.0005, "low": mid - 0.0005}
    ch = channel(h4, 150)
    assert ch is not None and ch[1] < ch[2], ch
    trend = {"t": x * 14400, "high": 1.0 + x * 0.001 + 0.0005, "low": 1.0 + x * 0.001 - 0.0005}
    assert channel(trend, 150) is None
    print("bennett_channel selfcheck ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-07")
    ap.add_argument("--end", default="2026-11-07")
    ap.add_argument("--symbols", default="EURUSD,GBPUSD,AUDUSD,XAUUSD,USDJPY,USDCHF,USDCAD")
    a = ap.parse_args()
    rows = sum((run(s, a.start, a.end) for s in a.symbols.split(",")), [])
    df = pd.DataFrame(rows)
    if df.empty:
        print("no trades")
    else:
        df["t"] = pd.to_datetime(df.t_entry, unit="s")
        print(df[["sym", "dir", "t", "entry", "sl", "tp", "why", "r"]].to_string())
        print(f"\n{len(df)} trades win {np.mean(df.r > 0):.0%} {df.r.mean():+.3f}R total {df.r.sum():+.1f}R  ${df.r.sum() * 500:+,.0f} @$500")

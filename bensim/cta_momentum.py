"""Daily trend-momentum strategy of the r/Forex "I profitably traded FX for a CTA" post (f09ujh, 2020; 59.9% in ~2 years,
32% win rate claimed). Judged on 25 FXCM pairs (19 never used here before) and our 7 ICMarkets pairs.

    python -m bensim.cta_momentum

Rules as posted, our mechanical reading fixed BEFORE the first run (do not tune on results):
  bars       daily, New York 17:00 close (parallax.ny_bars).
  trend      long only when close > SMA200 and SMA200 higher than 20 bars ago (his "200 as a general reference");
             short mirror.
  pullback   at least 2 of the last 5 bars before the signal closed below SMA10 (his "10 SMA as scanning indicator").
  momentum   the signal bar: closes above SMA10, bullish, range >= 1.2 x ATR(20) and body >= 60% of the range
             ("big candles in the direction of the overall trend").
  entry      buy stop 1 pip above the signal bar high, live for 3 bars; cancelled if the signal bar's midpoint
             trades first (the setup failed before triggering).
  stop       midpoint of the momentum bar (bars are >= 1.2 ATR so never "narrow").
  trail      after a pullback of >= 2 bars (bars that do not make a new high) price CLOSES above the high made before
             it -> stop moves to that pullback's low (never down). Repeat until stopped; 120-bar time stop.
v2 = the reading his annotated charts show (checked 2026-10-14, imgur images in the post), fixed before testing:
  trend      close > SMA200 (< for shorts) AND swing structure: the last two confirmed 5-bar fractal highs AND lows
             rising (falling) -- his "higher highs and higher lows" pictures.
  pullback   the 2 bars before the signal made lower highs (higher lows for shorts) -- "a pullback of 2 or more bars".
  momentum   any size: the first bar back in the trend direction that closes beyond the previous bar's high (low);
             his marked "momentum bars" are ordinary candles ending the pullback, not big ones.
  entry      stop order 1 pip beyond the bar, live up to 10 bars (his example filled 4 bars later) unless the
             midpoint trades first.
  stop       midpoint; beyond the whole bar when its range < 0.5 x ATR(20) ("narrow in relation to the recent range").
  trail      as v1.
Fills on daily bars, pessimistic: a fill day that also reaches the stop is a loss; gaps fill at the open.
Costs: the pair's median spread (FXCM real ask-bid, or our raw spread model + $7/lot) on entry and exit, in R.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import parallax as px
from .strategy import pip

MAJORS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCHF", "USDCAD"]


def daily(sym: str, source: str, start: str = "2021-06-01") -> tuple[pd.DataFrame, float]:
    p = px.fxcm_path(sym, start, None) if source == "fxcm" else px.Path.load(sym, start, None)
    b = px.ny_bars({"t": p.t, "open": p.o, "high": p.h, "low": p.lo, "close": p.c}, 24)
    cost = float(np.median(p.spr) + np.median(p.comm))  # round trip in price: spread at entry+exit ~ 1 spread + commission
    return b, cost


def _swings_rising(x: np.ndarray, sign: int) -> np.ndarray:
    """Per bar: the last two confirmed 5-bar fractal highs (sign=1 on highs) are rising, known 2 bars after each."""
    n, out, last = len(x), np.zeros(len(x), bool), []
    for i in range(n):
        j = i - 2
        if j >= 2 and x[j] > max(x[j - 2], x[j - 1], x[j + 1], x[j + 2]):
            last = (last + [x[j]])[-2:]
        out[i] = len(last) == 2 and (last[1] - last[0]) * sign > 0
    return out


def signals(b: pd.DataFrame, variant: str = "v1") -> pd.DataFrame:
    c, h, lo, o = b.close, b.high, b.low, b.open
    if variant == "v2":
        sma200 = c.rolling(200).mean()
        hh, hl = _swings_rising(h.to_numpy(), 1), _swings_rising(-lo.to_numpy(), -1)  # rising highs / rising lows
        lh, ll = _swings_rising(h.to_numpy(), -1), _swings_rising(-lo.to_numpy(), 1)
        up = (c > sma200) & hh & hl & (h.shift(1) < h.shift(2)) & (h.shift(2) < h.shift(3)) & (c > o) & (c > h.shift(1))
        dn = (c < sma200) & lh & ll & (lo.shift(1) > lo.shift(2)) & (lo.shift(2) > lo.shift(3)) & (c < o) & (c < lo.shift(1))
        s = b[up | dn].copy()
        s["dir"] = np.where(up[up | dn], 1, -1)
        return s
    sma10, sma200 = c.rolling(10).mean(), c.rolling(200).mean()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean().shift(1)  # the signal bar's own range must not set its yardstick
    rng = h - lo
    big = (rng >= 1.2 * atr) & ((c - o).abs() >= 0.6 * rng)
    below, above = (c < sma10).astype(int), (c > sma10).astype(int)
    up = (c > sma200) & (sma200 > sma200.shift(20)) & (below.shift(1).rolling(5).sum() >= 2) & big & (c > o) & (c > sma10)
    dn = (c < sma200) & (sma200 < sma200.shift(20)) & (above.shift(1).rolling(5).sum() >= 2) & big & (c < o) & (c < sma10)
    s = b[up | dn].copy()
    s["dir"] = np.where(up[up | dn], 1, -1)
    return s


def trade(b: pd.DataFrame, i: int, d: int, buffer: float, variant: str = "v1", atr: float = np.nan) -> tuple[float, float, int, int] | None:
    """(fill, exit price, fill bar, exit bar) for the signal at bar i, or None (never triggered)."""
    H, L, Op, C = b.high.to_numpy(), b.low.to_numpy(), b.open.to_numpy(), b.close.to_numpy()
    entry = (H[i] + buffer) if d == 1 else (L[i] - buffer)
    stop = (H[i] + L[i]) / 2
    if variant == "v2" and H[i] - L[i] < 0.5 * atr:  # narrow bar: stop beyond the whole bar
        stop = L[i] - buffer if d == 1 else H[i] + buffer
    for k in range(i + 1, min(i + (11 if variant == "v2" else 4), len(b))):
        if (H[k] >= entry) if d == 1 else (L[k] <= entry):  # triggered (a day also reaching the stop is a loss below)
            fill = max(entry, Op[k]) if d == 1 else min(entry, Op[k])
            break
        if (L[k] <= stop) if d == 1 else (H[k] >= stop):  # the midpoint traded before any trigger: setup failed
            return None
    else:
        return None
    if (L[k] <= stop) if d == 1 else (H[k] >= stop):  # filled and stopped the same day (order unknown): a loss
        return fill, stop, k, k
    ext, pb_low, pb_len, j = (H[k], np.inf, 0, k) if d == 1 else (L[k], -np.inf, 0, k)
    for j in range(k + 1, min(k + 121, len(b))):
        if (L[j] <= stop) if d == 1 else (H[j] >= stop):
            return fill, (min(stop, Op[j]) if d == 1 else max(stop, Op[j])), k, j
        if (H[j] > ext) if d == 1 else (L[j] < ext):  # new extreme: a pullback just ended?
            if pb_len >= 2 and ((C[j] > ext) if d == 1 else (C[j] < ext)):
                stop = max(stop, pb_low) if d == 1 else min(stop, pb_low)
            ext, pb_low, pb_len = (H[j], np.inf, 0) if d == 1 else (L[j], -np.inf, 0)
        else:
            pb_len += 1
            pb_low = min(pb_low, L[j]) if d == 1 else max(pb_low, H[j])
    return fill, C[j], k, j


def run(sym: str, source: str, variant: str = "v1", start: str = "2021-06-01") -> pd.DataFrame:
    b, cost = daily(sym, source, start)
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(), (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean().shift(1).to_numpy()
    rows = []
    for i, s in signals(b, variant).iterrows():
        d = int(s.dir)
        res = trade(b, int(i), d, pip(sym), variant, atr[int(i)])
        if res is None:
            continue
        fill, exit_px, k, j = res
        narrow = variant == "v2" and s.high - s.low < 0.5 * atr[int(i)]
        stop0 = ((s.low - pip(sym)) if d == 1 else (s.high + pip(sym))) if narrow else (s.high + s.low) / 2
        risk = abs(fill - stop0)
        if risk <= 0:
            continue
        rows.append((sym, source, variant, d, int(b.t_close.iloc[k]), int(b.t_close.iloc[j]), (exit_px - fill) * d / risk - cost / risk))
    return pd.DataFrame(rows, columns=["symbol", "source", "variant", "dir", "t_fill", "t_exit", "r"])


def report(t: pd.DataFrame, label: str) -> None:
    if t.empty:
        print(f"{label}: no trades")
        return
    t = t.sort_values("t_exit")
    r = t.r.to_numpy()
    eq = np.cumsum(r)
    ys = t.groupby(pd.to_datetime(t.t_fill, unit="s").dt.year).r.agg(["count", "sum"])
    pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
    print(f"{label:34} {len(r):4} trades win {np.mean(r > 0) * 100:4.1f}% avg {r.mean():+.3f}R PF {pf:4.2f} total {r.sum():+6.1f}R "
          f"maxDD {(np.maximum.accumulate(eq) - eq).max():5.1f}R t {r.mean() / r.std() * np.sqrt(len(r)):+.2f} | years+ "
          f"{(ys['sum'] > 0).sum()}/{len(ys)}  " + " ".join(f"{y % 100}:{v:+.1f}" for y, v in ys['sum'].items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    from .fxcm_history import OUT
    fx = sorted(f.stem.replace("_H1", "") for f in OUT.glob("*_H1.npz"))
    t_fx = pd.concat([run(s, "fxcm") for s in fx], ignore_index=True)
    t_db = pd.concat([run(s, "db") for s in MAJORS + ["XAUUSD"]], ignore_index=True)
    pd.concat([t_fx, t_db]).to_csv("data/cta_momentum_5y.csv", index=False)
    print("CTA trend-momentum, daily, rules fixed before running. R per trade net of spread (+$7/lot on ICMarkets data).")
    report(t_db, "ICMarkets 6 majors + gold")
    report(t_fx[t_fx.symbol.isin(MAJORS)], "FXCM 6 majors")
    report(t_fx[~t_fx.symbol.isin(MAJORS)], "FXCM 19 other pairs (untouched)")
    report(t_fx, "FXCM all 25 pairs")


if __name__ == "__main__":
    main()
